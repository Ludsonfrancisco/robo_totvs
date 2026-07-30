from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from flows.financeiro_medicao.bundle import build_bundle
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

    def build(self, runtime_root, source):
        return build_bundle(
            runtime_root=Path(runtime_root), source=source, window=self.window,
            scheduled_for=self.scheduled_for, started_at=self.started_at,
            finished_at=self.finished_at, image_revision="sha256:abc123",
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
