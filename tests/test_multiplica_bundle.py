from datetime import date, datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from flows.multiplica.bundle import build_bundle
from flows.multiplica.cycles import CycleWindow


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "multiplica"
        for name in ("inbox", "runtime"):
            (self.root / name).mkdir(parents=True)
        self.window = CycleWindow(
            date(2026, 7, 11),
            date(2026, 8, 10),
            date(2026, 7, 23),
        )

    def test_builds_complete_atomic_bundle(self):
        bundle_dir = build_bundle(
            runtime_root=self.root,
            window=self.window,
            summary_text="Cidade\tIIP\nTotal\t100\n",
            tooltip_bases_text=(
                "Cidade\tIndicador\tNumerador\tDenominador\t"
                "Rótulo numerador\tRótulo denominador\n"
                "Aracruz\tIIP\t1\t1\tNo Prazo\tTotal Produtivos\n"
            ),
            workbook_bytes=b"xlsx-sintetico",
            captured_at=datetime(
                2026, 7, 23, 23, 50, tzinfo=timezone.utc
            ),
        )

        manifest = json.loads(
            (bundle_dir / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["filters"]["executor"], "Dmais")
        self.assertEqual(len(manifest["summary_sha256"]), 64)
        self.assertEqual(len(manifest["tooltip_bases_sha256"]), 64)
        self.assertEqual(len(manifest["workbook_sha256"]), 64)
        self.assertTrue((bundle_dir / manifest["summary_file"]).is_file())
        self.assertTrue(
            (bundle_dir / manifest["tooltip_bases_file"]).is_file()
        )
        self.assertTrue((bundle_dir / manifest["workbook_file"]).is_file())

    def test_interruption_before_replace_keeps_inbox_empty(self):
        def interrupt():
            raise RuntimeError("interrompido")

        with self.assertRaisesRegex(RuntimeError, "interrompido"):
            build_bundle(
                runtime_root=self.root,
                window=self.window,
                summary_text="resumo",
                tooltip_bases_text="bases",
                workbook_bytes=b"xlsx",
                captured_at=datetime.now(timezone.utc),
                before_publish=interrupt,
            )

        self.assertEqual(list((self.root / "inbox").iterdir()), [])
