from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from flows.common.locks import LockUnavailable
from flows.financeiro_medicao.loga import CollectionError
from flows.financeiro_medicao.runner import run_once
from flows.financeiro_medicao.workbook import WorkbookInvalid


PAYLOAD_KEYS = {
    "success",
    "error_code",
    "run_id",
    "cycle_id",
    "mode",
    "started_at",
    "finished_at",
    "next_scheduled_for",
}


class FinanceiroMedicaoRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runtime_root = (
            Path(self.temporary.name) / "financeiro_medicao"
        )
        self.settings = SimpleNamespace(
            runtime_root=self.runtime_root,
            lock_wait_seconds=0,
            schedule_enabled=False,
            schedule_hour=0,
            schedule_minute=1,
            timezone="America/Sao_Paulo",
        )
        self.moment = datetime(
            2026,
            7,
            30,
            3,
            1,
            tzinfo=timezone(timedelta(hours=-3)),
        )

    @staticmethod
    @contextmanager
    def page_factory(_settings):
        yield object()

    def _run_once(self, collector, **overrides):
        arguments = {
            "settings": self.settings,
            "day": date(2026, 7, 30),
            "page_factory": self.page_factory,
            "collector": collector,
            "sleeper": lambda _: None,
            "clock": lambda: self.moment,
            "image_revision": "test-revision",
        }
        arguments.update(overrides)
        return run_once(**arguments)

    def test_auth_failure_does_not_retry_and_writes_exact_safe_payload(self):
        secret = "https://user:password@example.invalid/path?token=secret"
        collector = Mock(
            side_effect=CollectionError("AUTH_EXPIRED")
        )

        payload = self._run_once(collector)

        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "AUTH_EXPIRED")
        self.assertEqual(collector.call_count, 1)
        self.assertEqual(set(payload), PAYLOAD_KEYS)
        serialized = json.dumps(payload)
        self.assertNotIn(secret, serialized)
        persisted = json.loads(
            (self.runtime_root / "done.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted, payload)

    def test_minimal_settings_test_double_uses_nonblocking_locks(self):
        settings = Mock()
        settings.runtime_root = self.runtime_root
        collector = Mock(
            side_effect=CollectionError("AUTH_EXPIRED")
        )

        payload = run_once(
            settings=settings,
            day=date(2026, 7, 30),
            page_factory=self.page_factory,
            collector=collector,
            clock=lambda: self.moment,
        )

        self.assertEqual(payload["error_code"], "AUTH_EXPIRED")
        self.assertEqual(collector.call_count, 1)

    def test_only_transient_collection_errors_retry_exactly_three_times(self):
        for error_code in (
            "NAVIGATION_TIMEOUT",
            "DOWNLOAD_TIMEOUT",
            "DOWNLOAD_FAILED",
        ):
            with self.subTest(error_code=error_code):
                collector = Mock(
                    side_effect=CollectionError(error_code)
                )

                payload = self._run_once(collector)

                self.assertFalse(payload["success"])
                self.assertEqual(payload["error_code"], error_code)
                self.assertEqual(collector.call_count, 3)

    def test_success_collects_private_file_then_builds_bundle(self):
        collector_destinations = []

        def collector(_page, _window, _settings, destination):
            self.assertEqual(destination.parent, self.runtime_root / "runtime")
            self.assertFalse(destination.exists())
            collector_destinations.append(destination)
            destination.write_bytes(b"xlsx")
            return destination

        published = self.runtime_root / "inbox" / "run-123"
        builder = Mock(return_value=published)

        payload = self._run_once(collector, bundle_builder=builder)

        self.assertTrue(payload["success"])
        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["error_code"], "")
        self.assertEqual(payload["cycle_id"], "2026-07-11--2026-08-10")
        self.assertEqual(payload["mode"], "current")
        self.assertFalse(collector_destinations[0].exists())
        build_arguments = builder.call_args.kwargs
        self.assertEqual(build_arguments["source"], collector_destinations[0])
        self.assertEqual(build_arguments["scheduled_for"], self.moment)
        self.assertEqual(build_arguments["image_revision"], "test-revision")

    def test_creates_only_required_runtime_foundation(self):
        payload = self._run_once(
            Mock(side_effect=CollectionError("AUTH_EXPIRED"))
        )

        self.assertFalse(payload["success"])
        for name in ("inbox", "quarantine", "published", "runtime"):
            self.assertTrue((self.runtime_root / name).is_dir())

    def test_workbook_invalid_is_sanitized_and_not_retried(self):
        def collector(_page, _window, _settings, destination):
            destination.write_bytes(b"invalid")
            return destination

        builder = Mock(
            side_effect=WorkbookInvalid(
                "C:\\sensitive\\customer.xlsx has secret"
            )
        )

        payload = self._run_once(collector, bundle_builder=builder)

        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "WORKBOOK_INVALID")
        self.assertEqual(builder.call_count, 1)
        self.assertNotIn("sensitive", json.dumps(payload))

    def test_unexpected_exception_never_reaches_status(self):
        collector = Mock(
            side_effect=RuntimeError(
                "cookie=session; url=https://example.invalid?a=secret"
            )
        )

        payload = self._run_once(collector)

        self.assertEqual(payload["error_code"], "UNEXPECTED_ERROR")
        self.assertEqual(collector.call_count, 1)
        self.assertNotIn("cookie", json.dumps(payload).casefold())
        self.assertNotIn("https", json.dumps(payload).casefold())

    def test_private_cleanup_failure_preserves_primary_error_code(self):
        collector = Mock(
            side_effect=CollectionError("AUTH_EXPIRED")
        )

        with patch(
            "flows.financeiro_medicao.runner._remove_private_file",
            side_effect=OSError("C:\\sensitive\\download.xlsx"),
        ):
            payload = self._run_once(collector)

        self.assertEqual(payload["error_code"], "AUTH_EXPIRED")
        self.assertNotIn("sensitive", json.dumps(payload))

    def test_private_file_is_removed_when_collection_is_interrupted(self):
        destinations = []

        def collector(_page, _window, _settings, destination):
            destinations.append(destination)
            destination.write_bytes(b"private workbook")
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self._run_once(collector)

        self.assertEqual(len(destinations), 1)
        self.assertFalse(destinations[0].exists())

    def test_lock_failure_is_sanitized_without_opening_page(self):
        page_factory = Mock()

        with patch(
            "flows.financeiro_medicao.runner.file_lock",
            side_effect=LockUnavailable("sensitive lock path"),
        ):
            payload = self._run_once(Mock(), page_factory=page_factory)

        self.assertEqual(payload["error_code"], "LOCKED")
        page_factory.assert_not_called()

    def test_flow_lock_is_acquired_before_global_chromium_lock(self):
        events = []

        @contextmanager
        def recording_lock(path, *, wait_seconds):
            events.append(("enter", Path(path), wait_seconds))
            try:
                yield
            finally:
                events.append(("exit", Path(path), wait_seconds))

        def record_done(_runtime_root, _payload):
            events.append(("write-done",))

        with patch(
            "flows.financeiro_medicao.runner.file_lock",
            side_effect=recording_lock,
        ), patch(
            "flows.financeiro_medicao.runner._write_done",
            side_effect=record_done,
        ):
            self._run_once(
                Mock(side_effect=CollectionError("AUTH_EXPIRED"))
            )

        flow_lock = (
            self.runtime_root
            / "runtime"
            / "financeiro_medicao.lock"
        )
        global_lock = (
            self.runtime_root.parent / "runtime" / "chromium.lock"
        )
        self.assertEqual(
            events,
            [
                ("enter", flow_lock, 0),
                ("enter", global_lock, 0),
                ("exit", global_lock, 0),
                ("write-done",),
                ("exit", flow_lock, 0),
            ],
        )

    def test_done_json_replace_is_atomic_and_file_is_fsynced(self):
        original_replace = os.replace
        observed = []

        def inspect_replace(source, destination):
            source = Path(source)
            destination = Path(destination)
            observed.append(
                (
                    source.is_file(),
                    destination.exists(),
                    json.loads(source.read_text(encoding="utf-8")),
                )
            )
            original_replace(source, destination)

        with patch(
            "flows.financeiro_medicao.runner.os.replace",
            side_effect=inspect_replace,
        ), patch(
            "flows.financeiro_medicao.runner.os.fsync",
            wraps=os.fsync,
        ) as fsync:
            payload = self._run_once(
                Mock(side_effect=CollectionError("AUTH_EXPIRED"))
            )

        self.assertEqual(observed, [(True, False, payload)])
        self.assertGreaterEqual(fsync.call_count, 1)
        self.assertEqual(
            list(self.runtime_root.glob(".done.json.*.tmp")),
            [],
        )

    def test_next_schedule_uses_configured_timezone(self):
        self.settings.schedule_enabled = True
        self.settings.schedule_hour = 4
        self.settings.schedule_minute = 30

        payload = self._run_once(
            Mock(side_effect=CollectionError("AUTH_EXPIRED"))
        )

        self.assertEqual(
            payload["next_scheduled_for"],
            "2026-07-30T04:30:00-03:00",
        )
