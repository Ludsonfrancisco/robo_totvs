from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from flows.multiplica.config import Settings


class MultiplicaSettingsTests(unittest.TestCase):
    def test_runtime_paths_are_separate_from_routerbox(self):
        with TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "multiplica"
            settings = Settings.from_mapping(
                {
                    "MULTIPLICA_LOGA_URL": "https://example.invalid/indicadores",
                    "MULTIPLICA_RUNTIME_ROOT": str(runtime_root),
                    "MULTIPLICA_SCHEDULE_ENABLED": "false",
                }
            )

            self.assertEqual(settings.runtime_root.name, "multiplica")
            self.assertNotIn("routerbox_backlog", str(settings.runtime_root))
            self.assertFalse(settings.schedule_enabled)
            self.assertEqual(
                {path.name for path in runtime_root.iterdir()},
                {"auth", "inbox", "processed", "runtime"},
            )

    def test_rejects_non_https_url(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            Settings.from_mapping(
                {
                    "MULTIPLICA_LOGA_URL": "http://example.invalid",
                    "MULTIPLICA_RUNTIME_ROOT": "C:/tmp/multiplica",
                }
            )
