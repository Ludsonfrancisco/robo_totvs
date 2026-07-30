from datetime import date, datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from flows.financeiro_medicao import bundle
from flows.financeiro_medicao.bundle import (
    BundleDurabilityError,
    build_bundle,
)
from flows.financeiro_medicao.cycles import CycleWindow
from flows.financeiro_medicao.workbook import REQUIRED_HEADERS, SHEET_NAME, WorkbookInvalid


CANONICAL_ROW = (
    "123456", "11/07/2026 08:00:00", "PROTO-123", "Cliente Exemplo",
    "Maria da Silva", "Sao Paulo", "Centro", "Usuario Final",
    "Executor Exemplo", "15/07/2026 12:30:00", "Empresa Exemplo LTDA",
    "Empresa Exemplo", "Financeiro", "Medicao", "Pagamento", "Sem causa",
    "Encerrado", "Nao", "", 125.50,
)


class FinanceiroMedicaoBundleTests(unittest.TestCase):
    def setUp(self):
        self.window = CycleWindow(date(2026, 7, 11), date(2026, 8, 10), date(2026, 7, 11), date(2026, 7, 31), "current")
        self.scheduled_for = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)
        self.started_at = datetime(2026, 7, 31, 8, 1, tzinfo=timezone.utc)
        self.finished_at = datetime(2026, 7, 31, 8, 2, tzinfo=timezone.utc)

    def make_workbook(self, directory, *, valid=True):
        path = Path(directory) / "source.xlsx"
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = SHEET_NAME if valid else "Outra aba"
        worksheet.append(list(REQUIRED_HEADERS))
        worksheet.append(list(CANONICAL_ROW))
        workbook.save(path)
        workbook.close()
        return path

    def build(
        self,
        runtime_root,
        source,
        *,
        image_revision="sha256:abc123",
        run_id=None,
    ):
        arguments = dict(
            runtime_root=Path(runtime_root), source=source, window=self.window,
            scheduled_for=self.scheduled_for, started_at=self.started_at,
            finished_at=self.finished_at, image_revision=image_revision,
        )
        if run_id is not None:
            arguments["run_id"] = run_id
        return build_bundle(**arguments)

    def test_deterministic_bundle_reuses_only_valid_publication(self):
        self.assertTrue(
            hasattr(bundle, "BundleCollisionError"),
            "bundle must reject an inconsistent deterministic collision",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            source = self.make_workbook(directory)
            run_id = "a" * 32

            first = self.build(root, source, run_id=run_id)
            repeated = self.build(root, source, run_id=run_id)
            (first / "medicao_original.xlsx").write_bytes(b"corrupted")

            self.assertEqual(repeated, first)
            with self.assertRaises(bundle.BundleCollisionError):
                self.build(root, source, run_id=run_id)

    def test_validation_uses_one_private_workbook_snapshot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            run_id = "b" * 32
            published = self.build(
                root,
                self.make_workbook(directory),
                run_id=run_id,
            )
            original_validate = bundle.validate_workbook
            validated_sources = []

            def record_validation(source, query_start, query_end):
                validated_sources.append(source)
                self.assertFalse(source.closed)
                return original_validate(
                    source,
                    query_start,
                    query_end,
                )

            with patch.object(
                bundle,
                "validate_workbook",
                side_effect=record_validation,
            ):
                result = bundle.validate_published_bundle(
                    runtime_root=root,
                    run_id=run_id,
                    window=self.window,
                    scheduled_for=self.scheduled_for,
                )

            self.assertEqual(result, published)
            self.assertEqual(len(validated_sources), 1)
            self.assertFalse(
                isinstance(validated_sources[0], (str, Path))
            )
            self.assertTrue(validated_sources[0].closed)
            self.assertFalse(Path(validated_sources[0].name).exists())

    def test_validation_uses_open_snapshot_when_pathname_is_swapped(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            run_id = "f" * 32
            published = self.build(
                root,
                self.make_workbook(directory),
                run_id=run_id,
            )
            original_validate = bundle.validate_workbook
            decoy_path = root / "runtime" / "snapshot-decoy.xlsx"
            decoy_path.write_bytes(b"decoy")

            class NamedBytesIO(BytesIO):
                name = str(decoy_path)

            def swap_then_validate(source, query_start, query_end):
                decoy_path.write_bytes(b"swapped pathname")
                source.seek(0)
                return original_validate(
                    source,
                    query_start,
                    query_end,
                )

            with patch(
                "flows.common.safe_snapshot.tempfile.NamedTemporaryFile",
                return_value=NamedBytesIO(),
            ), patch.object(
                bundle,
                "validate_workbook",
                side_effect=swap_then_validate,
            ):
                result = bundle.validate_published_bundle(
                    runtime_root=root,
                    run_id=run_id,
                    window=self.window,
                    scheduled_for=self.scheduled_for,
                )

            self.assertEqual(result, published)

    def test_inspector_returns_validated_manifest_snapshot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            run_id = "1" * 32
            published = self.build(
                root,
                self.make_workbook(directory),
                run_id=run_id,
            )

            details = bundle.inspect_published_bundle(
                runtime_root=root,
                run_id=run_id,
                window=self.window,
                scheduled_for=self.scheduled_for,
            )

        self.assertEqual(details.path, published)
        self.assertEqual(details.manifest["run_id"], run_id)
        self.assertEqual(
            details.workbook_size,
            details.manifest["workbook_size"],
        )
        self.assertEqual(
            details.workbook_sha256,
            details.manifest["workbook_sha256"],
        )

    def test_independent_proof_is_durable_before_inbox_visibility(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            run_id = "2" * 32
            replacements = []
            original_replace = os.replace

            def record_replace(source, destination):
                replacements.append(Path(destination))
                return original_replace(source, destination)

            with patch.object(
                bundle.os,
                "replace",
                side_effect=record_replace,
            ):
                published = self.build(
                    root,
                    self.make_workbook(directory),
                    run_id=run_id,
                )

            proof = root / "runtime" / "proofs" / run_id
            details = bundle.inspect_publication_proof(
                runtime_root=root,
                run_id=run_id,
                window=self.window,
                scheduled_for=self.scheduled_for,
            )

        self.assertLess(
            replacements.index(proof),
            replacements.index(published),
        )
        self.assertEqual(details.path, proof)
        self.assertEqual(details.manifest["run_id"], run_id)

    def test_consumed_bundle_is_not_republished_when_proof_exists(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            run_id = "3" * 32
            source = self.make_workbook(directory)
            published = self.build(root, source, run_id=run_id)
            consumed = Path(directory) / "consumed" / run_id
            consumed.parent.mkdir()
            os.replace(published, consumed)

            repeated = self.build(root, source, run_id=run_id)

            self.assertEqual(
                repeated,
                bundle.publication_proof_path(root, run_id),
            )
            self.assertTrue(repeated.is_dir())
            self.assertFalse(published.exists())

    def test_validation_rejects_workbook_swap_during_snapshot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            run_id = "c" * 32
            published = self.build(
                root,
                self.make_workbook(directory),
                run_id=run_id,
            )
            workbook_path = published / "medicao_original.xlsx"
            replacement = root / "runtime" / "replacement.xlsx"
            replacement.write_bytes(workbook_path.read_bytes())
            original_validate = bundle.validate_workbook

            def swap_then_validate(path, query_start, query_end):
                os.replace(replacement, workbook_path)
                return original_validate(path, query_start, query_end)

            with patch.object(
                bundle,
                "validate_workbook",
                side_effect=swap_then_validate,
            ), self.assertRaises(bundle.BundleCollisionError):
                bundle.validate_published_bundle(
                    runtime_root=root,
                    run_id=run_id,
                    window=self.window,
                    scheduled_for=self.scheduled_for,
                )

    def test_validation_rejects_published_workbook_symlink(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            run_id = "d" * 32
            published = self.build(
                root,
                self.make_workbook(directory),
                run_id=run_id,
            )
            workbook_path = published / "medicao_original.xlsx"
            outside = Path(directory) / "outside.xlsx"
            outside.write_bytes(workbook_path.read_bytes())
            workbook_path.unlink()
            try:
                workbook_path.symlink_to(outside)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")

            with self.assertRaises(bundle.BundleCollisionError):
                bundle.validate_published_bundle(
                    runtime_root=root,
                    run_id=run_id,
                    window=self.window,
                    scheduled_for=self.scheduled_for,
                )

    def test_windows_validation_fails_closed_on_simulated_symlink(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            run_id = "e" * 32
            published = self.build(
                root,
                self.make_workbook(directory),
                run_id=run_id,
            )
            workbook_path = published / "medicao_original.xlsx"
            original_lstat = Path.lstat

            def report_workbook_symlink(path):
                metadata = original_lstat(path)
                if Path(path) == workbook_path:
                    values = list(metadata)
                    values[0] = stat.S_IFLNK | 0o777
                    return os.stat_result(values)
                return metadata

            with patch.object(
                Path,
                "lstat",
                new=report_workbook_symlink,
            ), self.assertRaises(bundle.BundleCollisionError):
                bundle.validate_published_bundle(
                    runtime_root=root,
                    run_id=run_id,
                    window=self.window,
                    scheduled_for=self.scheduled_for,
                )

    def test_publishes_canonical_manifest_with_hash_of_published_workbook(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            published = self.build(root, self.make_workbook(directory))
            manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
            workbook = published / "medicao_original.xlsx"
            workbook_size = workbook.stat().st_size
            workbook_sha256 = hashlib.sha256(workbook.read_bytes()).hexdigest()

        self.assertEqual(published.parent.name, "inbox")
        self.assertEqual(manifest, {
            "schema_version": 1, "source": "LOGA", "flow": "financeiro_medicao",
            "run_id": published.name, "cycle_id": "2026-07-11--2026-08-10",
            "cycle_start": "2026-07-11", "cycle_close": "2026-08-10", "mode": "current",
            "scheduled_for": self.scheduled_for.isoformat(), "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(), "query_start": "2026-07-11",
            "query_end": "2026-07-31", "image_revision": "sha256:abc123", "status": "success",
            "workbook_file": "medicao_original.xlsx", "workbook_size": workbook_size,
            "workbook_sha256": workbook_sha256, "row_count": 1,
            "sheet_name": SHEET_NAME, "headers": list(REQUIRED_HEADERS),
        })

    def test_preserves_source_and_publishes_a_distinct_copy(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            source = self.make_workbook(directory)
            original = source.read_bytes()
            published = self.build(root, source)

            self.assertTrue(source.is_file())
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual((published / "medicao_original.xlsx").read_bytes(), original)
            self.assertNotEqual((published / "medicao_original.xlsx").resolve(), source.resolve())

    def test_replace_failure_publishes_nothing_and_removes_temp(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            source = self.make_workbook(directory)
            with patch("flows.financeiro_medicao.bundle.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    self.build(root, source)

            self.assertEqual(list((root / "inbox").iterdir()), [])
            self.assertEqual(list((root / "runtime").iterdir()), [])

    def test_invalid_workbook_publishes_nothing_and_removes_temp(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            with self.assertRaises(WorkbookInvalid):
                self.build(root, self.make_workbook(directory, valid=False))

            self.assertEqual(list((root / "inbox").iterdir()), [])
            self.assertEqual(list((root / "runtime").iterdir()), [])

    def test_rejects_invalid_metadata_before_creating_publication_directories(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            source = self.make_workbook(directory)
            invalid_cases = (
                {"scheduled_for": self.scheduled_for.replace(tzinfo=None)},
                {"finished_at": self.started_at.replace(minute=0)},
                {"image_revision": ""},
                {"runtime_root": Path(directory) / "wrong_root"},
            )
            for changes in invalid_cases:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    kwargs = dict(runtime_root=root, source=source, window=self.window,
                        scheduled_for=self.scheduled_for, started_at=self.started_at,
                        finished_at=self.finished_at, image_revision="sha256:abc123")
                    kwargs.update(changes)
                    build_bundle(**kwargs)
            self.assertFalse(root.exists())

    def test_two_builds_create_distinct_run_ids_and_packages(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            source = self.make_workbook(directory)
            first = self.build(root, source)
            second = self.build(root, source)

            self.assertNotEqual(first.name, second.name)
            self.assertRegex(first.name, r"^[0-9a-f]{32}$")
            self.assertRegex(second.name, r"^[0-9a-f]{32}$")
            self.assertTrue((first / "manifest.json").is_file())
            self.assertTrue((second / "manifest.json").is_file())

    def test_normalizes_image_revision_before_writing_manifest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            published = self.build(root, self.make_workbook(directory), image_revision="  sha256:abc123  ")

            manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["image_revision"], "sha256:abc123")

    def test_stops_workbook_copy_when_streamed_size_exceeds_limit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            source = Path(directory) / "oversized.xlsx"
            source.write_bytes(b"0123456789")
            written = 0
            original_open = Path.open

            class CountingWriter:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.stream.close()

                def write(self, data):
                    nonlocal written
                    written += len(data)
                    return self.stream.write(data)

                def __getattr__(self, name):
                    return getattr(self.stream, name)

            def count_workbook_writes(path, mode="r", *args, **kwargs):
                stream = original_open(path, mode, *args, **kwargs)
                if path.name == "medicao_original.xlsx" and mode == "wb":
                    return CountingWriter(stream)
                return stream

            with (
                patch("flows.financeiro_medicao.bundle.MAX_WORKBOOK_BYTES", 5, create=True),
                patch("flows.financeiro_medicao.bundle._CHUNK_SIZE", 3),
                patch("flows.financeiro_medicao.bundle.Path.open", new=count_workbook_writes),
            ):
                with self.assertRaises(WorkbookInvalid):
                    self.build(root, source)

            self.assertEqual(written, 3)
            self.assertEqual(list((root / "inbox").iterdir()), [])
            self.assertEqual(list((root / "runtime").iterdir()), [])

    def test_directory_fsync_opens_fsyncs_and_closes_descriptor_on_posix(self):
        directory = Path("directory-to-sync")
        with (
            patch("flows.financeiro_medicao.bundle.os.name", "posix"),
            patch("flows.financeiro_medicao.bundle.os.O_DIRECTORY", 0x10000, create=True),
            patch("flows.financeiro_medicao.bundle.os.open", return_value=71) as open_directory,
            patch("flows.financeiro_medicao.bundle.os.fsync") as fsync,
            patch("flows.financeiro_medicao.bundle.os.close") as close,
        ):
            from flows.financeiro_medicao.bundle import _fsync_directory

            _fsync_directory(directory)

        flags = open_directory.call_args.args[1]
        self.assertEqual(open_directory.call_args.args[0], directory)
        self.assertEqual(flags & 0x10000, 0x10000)
        self.assertEqual(fsync.call_args.args, (71,))
        self.assertEqual(close.call_args.args, (71,))

    def test_directory_sync_order_brackets_atomic_replace(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            (root / "runtime").mkdir(parents=True)
            (root / "inbox").mkdir()
            events = []
            original_replace = os.replace

            def record_sync(path):
                events.append(("sync", Path(path).name))

            def record_replace(source, destination):
                events.append(
                    ("replace", Path(source), Path(destination))
                )
                original_replace(source, destination)

            with (
                patch("flows.financeiro_medicao.bundle._fsync_directory", side_effect=record_sync),
                patch("flows.financeiro_medicao.bundle.os.replace", side_effect=record_replace),
            ):
                self.build(root, self.make_workbook(directory))

        replacements = [
            event for event in events if event[0] == "replace"
        ]
        self.assertEqual(len(replacements), 4)
        self.assertEqual(
            replacements[0][2].parent.name,
            "proofs",
        )
        self.assertEqual(
            replacements[1][2].parent.name,
            "inbox",
        )
        self.assertEqual(
            replacements[2][2].name,
            "publication.json",
        )
        self.assertEqual(
            replacements[3][2].parent.name,
            "inbox",
        )
        proof_replace = events.index(replacements[0])
        pending_replace = events.index(replacements[1])
        commit_replace = events.index(replacements[2])
        inbox_replace = events.index(replacements[3])
        self.assertLess(proof_replace, pending_replace)
        self.assertLess(pending_replace, commit_replace)
        self.assertLess(commit_replace, inbox_replace)
        self.assertIn(
            ("sync", "proofs"),
            events[proof_replace + 1 : pending_replace],
        )
        self.assertEqual(
            events[inbox_replace + 1 : inbox_replace + 3],
            [("sync", "inbox"), ("sync", "runtime")],
        )

    def test_initial_runtime_structure_is_synced_before_publication(self):
        with TemporaryDirectory() as directory:
            parent = Path(directory) / "mounted-parent"
            parent.mkdir()
            root = parent / "financeiro_medicao"
            events = []
            original_replace = os.replace

            def record_sync(path):
                events.append(("sync", Path(path).name))

            def record_replace(source, destination):
                events.append(
                    ("replace", Path(source), Path(destination))
                )
                original_replace(source, destination)

            with (
                patch("flows.financeiro_medicao.bundle._fsync_directory", side_effect=record_sync),
                patch("flows.financeiro_medicao.bundle.os.replace", side_effect=record_replace),
            ):
                self.build(root, self.make_workbook(directory))

        self.assertGreaterEqual(len(events), 11)
        self.assertEqual(events[:6], [
            ("sync", "financeiro_medicao"), ("sync", "mounted-parent"),
            ("sync", "runtime"), ("sync", "financeiro_medicao"),
            ("sync", "inbox"), ("sync", "financeiro_medicao"),
        ])
        replacements = [
            event for event in events if event[0] == "replace"
        ]
        self.assertEqual(
            [Path(event[2]).parent.name for event in replacements],
            [
                "proofs",
                "inbox",
                replacements[0][2].name,
                "inbox",
            ],
        )

    def test_rejects_missing_runtime_parent_without_creating_it(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "missing-parent" / "financeiro_medicao"

            with self.assertRaises(ValueError):
                self.build(root, self.make_workbook(directory))

            self.assertFalse(root.parent.exists())

    def test_post_replace_directory_sync_failure_keeps_published_bundle(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            (root / "runtime").mkdir(parents=True)
            (root / "inbox").mkdir()
            sync_calls = []
            inbox_syncs = 0

            def fail_inbox_sync(path):
                nonlocal inbox_syncs
                name = Path(path).name
                sync_calls.append(name)
                if name == "inbox":
                    inbox_syncs += 1
                if name == "inbox" and inbox_syncs == 2:
                    raise OSError("inbox directory fsync failed")

            with patch("flows.financeiro_medicao.bundle._fsync_directory", side_effect=fail_inbox_sync):
                with self.assertRaises(BundleDurabilityError) as raised:
                    self.build(root, self.make_workbook(directory))

            self.assertEqual(sync_calls[-1], "inbox")
            packages = list((root / "inbox").iterdir())
            self.assertEqual(len(packages), 1)
            self.assertEqual(raised.exception.published, packages[0])
            self.assertEqual(
                str(raised.exception),
                "BUNDLE_DURABILITY_FAILED",
            )
            self.assertIsInstance(
                raised.exception.__cause__,
                OSError,
            )
            self.assertTrue((packages[0] / "manifest.json").is_file())
            proof = (
                root
                / "runtime"
                / "proofs"
                / packages[0].name
            )
            self.assertTrue(
                (proof / "medicao_original.xlsx").is_file()
            )
            self.assertTrue((proof / "manifest.json").is_file())

    def test_post_replace_sync_does_not_absorb_termination_signals(self):
        for termination in (KeyboardInterrupt(), SystemExit(23)):
            with self.subTest(termination=type(termination).__name__):
                with TemporaryDirectory() as directory:
                    root = Path(directory) / "financeiro_medicao"
                    (root / "runtime").mkdir(parents=True)
                    (root / "inbox").mkdir()
                    def interrupt_after_replace(path):
                        if Path(path).name == "inbox":
                            raise termination

                    with patch(
                        "flows.financeiro_medicao.bundle._fsync_directory",
                        side_effect=interrupt_after_replace,
                    ), self.assertRaises(type(termination)) as raised:
                        self.build(root, self.make_workbook(directory))

                    packages = list((root / "inbox").iterdir())
                    self.assertEqual(len(packages), 1)
                    if isinstance(termination, SystemExit):
                        self.assertEqual(raised.exception.code, 23)

    def test_workbook_copy_write_failure_cleans_temp_and_preserves_outside_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            source = self.make_workbook(directory)
            outside = Path(directory) / "must_not_delete.txt"
            outside.write_text("keep", encoding="utf-8")
            original_open = Path.open

            class FailingWriter:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.stream.close()

                def write(self, data):
                    raise OSError("workbook write failed")

                def __getattr__(self, name):
                    return getattr(self.stream, name)

            def open_with_write_failure(path, mode="r", *args, **kwargs):
                stream = original_open(path, mode, *args, **kwargs)
                if path.name == "medicao_original.xlsx" and mode == "wb":
                    return FailingWriter(stream)
                return stream

            with patch("flows.financeiro_medicao.bundle.Path.open", new=open_with_write_failure):
                with self.assertRaisesRegex(OSError, "workbook write failed"):
                    self.build(root, source)

            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
            self.assertEqual(list((root / "inbox").iterdir()), [])
            self.assertEqual(list((root / "runtime").iterdir()), [])

    def test_workbook_fsync_failure_cleans_temp_and_preserves_outside_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            source = self.make_workbook(directory)
            outside = Path(directory) / "must_not_delete.txt"
            outside.write_text("keep", encoding="utf-8")
            with patch("flows.financeiro_medicao.bundle.os.fsync", side_effect=OSError("workbook fsync failed")):
                with self.assertRaisesRegex(OSError, "workbook fsync failed"):
                    self.build(root, source)

            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
            self.assertEqual(list((root / "inbox").iterdir()), [])
            self.assertEqual(list((root / "runtime").iterdir()), [])

    def test_manifest_fsync_failure_cleans_temp_and_preserves_outside_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "financeiro_medicao"
            source = self.make_workbook(directory)
            outside = Path(directory) / "must_not_delete.txt"
            outside.write_text("keep", encoding="utf-8")
            with patch(
                "flows.financeiro_medicao.bundle.os.fsync",
                side_effect=(None, OSError("manifest fsync failed")),
            ):
                with self.assertRaisesRegex(OSError, "manifest fsync failed"):
                    self.build(root, source)

            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
            self.assertEqual(list((root / "inbox").iterdir()), [])
            self.assertEqual(list((root / "runtime").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
