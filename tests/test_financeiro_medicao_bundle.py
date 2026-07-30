from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

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

    def build(self, runtime_root, source, *, image_revision="sha256:abc123"):
        return build_bundle(
            runtime_root=Path(runtime_root), source=source, window=self.window,
            scheduled_for=self.scheduled_for, started_at=self.started_at,
            finished_at=self.finished_at, image_revision=image_revision,
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

            def record_sync(path):
                events.append(("sync", Path(path).name))

            def record_replace(source, destination):
                events.append(("replace", Path(source).name, Path(destination).name))

            with (
                patch("flows.financeiro_medicao.bundle._fsync_directory", side_effect=record_sync),
                patch("flows.financeiro_medicao.bundle.os.replace", side_effect=record_replace),
            ):
                self.build(root, self.make_workbook(directory))

        self.assertEqual([event[0] for event in events], ["sync", "sync", "replace", "sync", "sync"])
        self.assertEqual(
            [events[0][1], events[1][1], events[3][1], events[4][1]],
            [events[2][1], "runtime", "inbox", "runtime"],
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
                events.append(("replace", Path(source).name, Path(destination).name))
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
        self.assertEqual(events[6][0], "sync")
        self.assertEqual(events[7], ("sync", "runtime"))
        self.assertEqual(events[8][0], "replace")

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

            def fail_inbox_sync(path):
                name = Path(path).name
                sync_calls.append(name)
                if name == "inbox":
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
            self.assertEqual(list((root / "runtime").iterdir()), [])

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
