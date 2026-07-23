from contextlib import contextmanager
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from flows.multiplica.config import Settings
from flows.multiplica.loga import CollectionError
from flows.multiplica.runner import AlreadyRunning, run_once


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.settings = Settings.from_mapping(
            {
                "MULTIPLICA_LOGA_URL": "https://example.invalid/indicadores",
                "MULTIPLICA_RUNTIME_ROOT": str(
                    Path(self.temp_dir.name) / "multiplica"
                ),
                "MULTIPLICA_SCHEDULE_ENABLED": "false",
            }
        )

    @staticmethod
    @contextmanager
    def page_factory(_settings):
        yield object()

    def test_lock_prevents_concurrent_run(self):
        lock = self.settings.runtime_root / "runtime" / "run_multiplica.lock"
        lock.write_text("ocupado", encoding="utf-8")
        with self.assertRaises(AlreadyRunning):
            run_once(
                settings=self.settings,
                day=date(2026, 7, 23),
                page_factory=self.page_factory,
                collector=lambda *_: None,
            )

    def test_auth_expired_is_not_retried(self):
        attempts = []

        def collector(*_):
            attempts.append(1)
            raise CollectionError("AUTH_EXPIRED")

        result = run_once(
            settings=self.settings,
            day=date(2026, 7, 23),
            page_factory=self.page_factory,
            collector=collector,
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "AUTH_EXPIRED")
        self.assertEqual(len(attempts), 1)

    def test_timeout_retries_at_most_three_times(self):
        attempts = []

        def collector(*_):
            attempts.append(1)
            if len(attempts) < 3:
                raise CollectionError("DOWNLOAD_TIMEOUT")
            return Path("pacote")

        result = run_once(
            settings=self.settings,
            day=date(2026, 7, 23),
            page_factory=self.page_factory,
            collector=collector,
        )

        self.assertTrue(result["success"])
        self.assertEqual(len(attempts), 3)
        self.assertFalse(
            (
                self.settings.runtime_root
                / "runtime"
                / "run_multiplica.lock"
            ).exists()
        )

    def test_playwright_timeout_is_retried(self):
        attempts = []

        def collector(*_):
            attempts.append(1)
            if len(attempts) < 3:
                raise PlaywrightTimeoutError("tempo esgotado")
            return Path("pacote")

        result = run_once(
            settings=self.settings,
            day=date(2026, 7, 23),
            page_factory=self.page_factory,
            collector=collector,
        )

        self.assertTrue(result["success"])
        self.assertEqual(len(attempts), 3)
