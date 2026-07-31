from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import inspect
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from flows.common.locks import LockUnavailable, file_lock
from flows.financeiro_medicao.bundle import (
    BundleCollisionError,
    BundleDurabilityError,
)
from flows.financeiro_medicao.loga import CollectionError
from flows.financeiro_medicao import events, runner
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

    def _write_event_journal(self, event_id, payload):
        path = runner.event_result_path(self.runtime_root, event_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": event_id,
                    "scheduled_for": self.moment.isoformat(),
                    "payload": payload,
                }
            ),
            encoding="utf-8",
        )
        return path

    def _terminal_payload(self):
        return {
            "success": False,
            "error_code": "AUTH_EXPIRED",
            "run_id": None,
            "cycle_id": "2026-07-11--2026-08-10",
            "mode": "current",
            "started_at": self.moment.isoformat(),
            "finished_at": self.moment.isoformat(),
            "next_scheduled_for": None,
        }

    def test_scheduled_identity_is_stable_and_timezone_sensitive(self):
        self.assertTrue(
            hasattr(runner, "scheduled_event_identity"),
            "runner must expose deterministic scheduled identity",
        )
        same = runner.scheduled_event_identity(self.moment)
        repeated = runner.scheduled_event_identity(self.moment)
        utc = runner.scheduled_event_identity(
            self.moment.astimezone(timezone.utc)
        )

        self.assertEqual(same, repeated)
        self.assertNotEqual(same, utc)
        self.assertRegex(same[0], r"^[0-9a-f]{32}$")
        self.assertRegex(same[1], r"^[0-9a-f]{32}$")
        self.assertNotEqual(same[0], same[1])

    def test_scheduled_result_journal_is_durable_before_flow_unlock(self):
        self.assertIn(
            "event_id",
            inspect.signature(run_once).parameters,
            "scheduled run must accept an event identity",
        )
        event_id, _ = runner.scheduled_event_identity(self.moment)
        journal = (
            self.runtime_root
            / "runtime"
            / "events"
            / f"{event_id}.result.json"
        )
        flow_unlock_observations = []

        @contextmanager
        def recording_lock(path, *, wait_seconds):
            try:
                yield
            finally:
                if Path(path).name == "financeiro_medicao.lock":
                    flow_unlock_observations.append(journal.exists())

        with patch(
            "flows.financeiro_medicao.runner.file_lock",
            side_effect=recording_lock,
        ):
            payload = self._run_once(
                Mock(side_effect=CollectionError("AUTH_EXPIRED")),
                event_id=event_id,
                scheduled_for=self.moment,
            )

        persisted = json.loads(journal.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), PAYLOAD_KEYS)
        self.assertEqual(
            persisted,
            {
                "schema_version": 1,
                "event_id": event_id,
                "scheduled_for": self.moment.isoformat(),
                "payload": payload,
            },
        )
        self.assertEqual(flow_unlock_observations, [True])

    def test_inconsistent_scheduled_bundle_is_journaled_as_terminal(self):
        event_id, run_id = runner.scheduled_event_identity(self.moment)
        published = self.runtime_root / "inbox" / run_id
        with patch.object(
            events,
            "inspect_published_bundle",
            side_effect=BundleCollisionError(published),
        ):
            try:
                payload = runner.reconcile_published_event(
                    self.settings,
                    event_id,
                    self.moment,
                )
            except BundleCollisionError:
                self.fail(
                    "deterministic collision must become a terminal journal"
                )

        self.assertEqual(payload["error_code"], "BUNDLE_COLLISION")
        self.assertFalse(payload["success"])
        self.assertEqual(set(payload), PAYLOAD_KEYS)
        persisted = runner.read_event_result(
            self.runtime_root,
            event_id,
            self.moment,
        )
        self.assertEqual(persisted, payload)

    def test_event_journal_rejects_semantically_invalid_payloads(self):
        event_id, run_id = runner.scheduled_event_identity(self.moment)
        cases = {
            "success-with-error": {"success": True},
            "failure-without-error": {"error_code": ""},
            "unknown-error": {"error_code": "SECRET_INTERNAL_ERROR"},
            "wrong-cycle": {"cycle_id": "2026-01-01--2026-01-31"},
            "wrong-mode": {"mode": "closed"},
            "naive-start": {
                "started_at": self.moment.replace(tzinfo=None).isoformat()
            },
            "finish-before-start": {
                "finished_at": (
                    self.moment - timedelta(seconds=1)
                ).isoformat()
            },
            "invalid-next": {"next_scheduled_for": "tomorrow"},
            "next-before-finish": {
                "next_scheduled_for": self.moment.isoformat()
            },
            "arbitrary-run-id": {"run_id": "run-from-another-event"},
            "unrecognized-partial-publication": {"run_id": run_id},
            "bool-cycle": {"cycle_id": True},
        }

        for name, changes in cases.items():
            with self.subTest(name=name):
                payload = self._terminal_payload()
                payload.update(changes)
                self._write_event_journal(event_id, payload)

                with self.assertRaisesRegex(
                    ValueError,
                    "Invalid event result journal",
                ):
                    runner.read_event_result(
                        self.runtime_root,
                        event_id,
                        self.moment,
                    )

        path = self._write_event_journal(
            event_id,
            self._terminal_payload(),
        )
        journal = json.loads(path.read_text(encoding="utf-8"))
        journal["untrusted"] = True
        path.write_text(json.dumps(journal), encoding="utf-8")
        with self.assertRaisesRegex(
            ValueError,
            "Invalid event result journal",
        ):
            runner.read_event_result(
                self.runtime_root,
                event_id,
                self.moment,
            )

    def test_success_journal_requires_matching_validated_bundle(self):
        event_id, run_id = runner.scheduled_event_identity(self.moment)
        payload = self._terminal_payload()
        payload.update(
            success=True,
            error_code="",
            run_id=run_id,
        )
        self._write_event_journal(event_id, payload)
        published = self.runtime_root / "inbox" / run_id
        details = runner.PublishedBundleDetails(
            path=published,
            manifest={},
            workbook_size=1,
            workbook_sha256="0" * 64,
        )

        with patch.object(
            events,
            "inspect_published_bundle",
            return_value=details,
        ) as inspect_bundle:
            result = runner.read_event_result(
                self.runtime_root,
                event_id,
                self.moment,
            )

        self.assertEqual(result, payload)
        self.assertEqual(
            inspect_bundle.call_args.kwargs["run_id"],
            run_id,
        )
        self.assertEqual(
            inspect_bundle.call_args.kwargs["scheduled_for"],
            self.moment,
        )
        self.assertEqual(
            inspect_bundle.call_args.kwargs["expected_result"],
            payload,
        )

        with patch.object(
            events,
            "inspect_published_bundle",
            return_value=None,
        ), self.assertRaisesRegex(
            ValueError,
            "Invalid event result journal",
        ):
            runner.read_event_result(
                self.runtime_root,
                event_id,
                self.moment,
            )

    def test_scheduled_success_uses_proof_after_immediate_consumption(self):
        event_id, run_id = runner.scheduled_event_identity(self.moment)
        consumed = Path(self.temporary.name) / "consumed" / run_id
        manifest = {
            "run_id": run_id,
            "cycle_id": "2026-07-11--2026-08-10",
            "mode": "current",
            "started_at": self.moment.isoformat(),
            "finished_at": self.moment.isoformat(),
        }
        details = runner.PublishedBundleDetails(
            path=self.runtime_root / "runtime" / "proofs" / run_id,
            manifest=manifest,
            workbook_size=1,
            workbook_sha256="0" * 64,
        )

        def collector(_page, _window, _settings, destination):
            destination.write_bytes(b"download")
            return destination

        def consume_immediately(**arguments):
            published = arguments["runtime_root"] / "inbox" / run_id
            published.mkdir(parents=True)
            consumed.parent.mkdir()
            os.replace(published, consumed)
            return published

        with patch.object(
            runner,
            "inspect_committed_publication",
            return_value=details,
        ) as inspect_proof:
            payload = self._run_once(
                collector,
                bundle_builder=consume_immediately,
                event_id=event_id,
                scheduled_for=self.moment,
            )

        self.assertTrue(payload["success"])
        self.assertEqual(payload["run_id"], run_id)
        inspect_proof.assert_called_once()

    def test_transient_lock_journal_is_semantically_valid(self):
        event_id, _ = runner.scheduled_event_identity(self.moment)
        payload = self._terminal_payload()
        payload["error_code"] = "LOCKED"
        self._write_event_journal(event_id, payload)

        self.assertEqual(
            runner.read_event_result(
                self.runtime_root,
                event_id,
                self.moment,
            ),
            payload,
        )

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

    def test_published_bundle_durability_failure_preserves_run_id_without_retry(self):
        def collector(_page, _window, _settings, destination):
            destination.write_bytes(b"xlsx")
            return destination

        published = self.runtime_root / "inbox" / "durable-run-123"
        builder = Mock(
            side_effect=BundleDurabilityError(published)
        )

        payload = self._run_once(collector, bundle_builder=builder)

        self.assertFalse(payload["success"])
        self.assertEqual(
            payload["error_code"],
            "BUNDLE_DURABILITY_FAILED",
        )
        self.assertEqual(payload["run_id"], "durable-run-123")
        self.assertEqual(builder.call_count, 1)

    def test_cleanup_failure_preserves_published_run_id_from_cause(self):
        def collector(_page, _window, _settings, destination):
            destination.write_bytes(b"xlsx")
            return destination

        published = self.runtime_root / "inbox" / "durable-run-456"
        builder = Mock(
            side_effect=BundleDurabilityError(published)
        )

        with patch(
            "flows.financeiro_medicao.runner._remove_private_file",
            side_effect=OSError("C:\\sensitive\\private.xlsx"),
        ):
            payload = self._run_once(
                collector,
                bundle_builder=builder,
            )

        self.assertEqual(
            payload["error_code"],
            "DOWNLOAD_TEMP_CLEANUP_FAILED",
        )
        self.assertEqual(payload["run_id"], "durable-run-456")
        self.assertEqual(builder.call_count, 1)
        self.assertNotIn("sensitive", json.dumps(payload).casefold())

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

    def test_private_cleanup_failure_takes_safe_operational_precedence(self):
        collector = Mock(
            side_effect=CollectionError("AUTH_EXPIRED")
        )

        with patch(
            "flows.financeiro_medicao.runner._remove_private_file",
            side_effect=OSError("C:\\sensitive\\download.xlsx"),
        ):
            payload = self._run_once(collector)

        self.assertEqual(
            payload["error_code"],
            "DOWNLOAD_TEMP_CLEANUP_FAILED",
        )
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

    def test_cleanup_failure_never_converts_termination_signals(self):
        for termination in (KeyboardInterrupt(), SystemExit(17)):
            with self.subTest(termination=type(termination).__name__):
                def collector(
                    _page,
                    _window,
                    _settings,
                    destination,
                ):
                    destination.write_bytes(b"private workbook")
                    raise termination

                with patch(
                    "flows.financeiro_medicao.runner._remove_private_file",
                    side_effect=OSError("cleanup failed"),
                ), self.assertRaises(type(termination)) as raised:
                    self._run_once(collector)

                if isinstance(termination, SystemExit):
                    self.assertEqual(raised.exception.code, 17)

    def test_lock_failure_is_sanitized_without_opening_page(self):
        page_factory = Mock()

        with patch(
            "flows.financeiro_medicao.runner.file_lock",
            side_effect=LockUnavailable("sensitive lock path"),
        ):
            payload = self._run_once(Mock(), page_factory=page_factory)

        self.assertEqual(payload["error_code"], "LOCKED")
        page_factory.assert_not_called()

    def test_flow_contention_returns_locked_without_replacing_canonical_status(self):
        canonical = (
            b'{"success":true,"error_code":"","run_id":"canonical"}\n'
        )
        self.runtime_root.mkdir()
        done = self.runtime_root / "done.json"
        done.write_bytes(canonical)
        flow_lock = (
            self.runtime_root
            / "runtime"
            / "financeiro_medicao.lock"
        )

        with file_lock(flow_lock, wait_seconds=0):
            payload = self._run_once(Mock())

        self.assertEqual(payload["error_code"], "LOCKED")
        self.assertFalse(payload["success"])
        self.assertEqual(done.read_bytes(), canonical)

    def test_cleanup_failure_stops_retry_and_hides_transient_primary(self):
        collector = Mock(
            side_effect=CollectionError("DOWNLOAD_TIMEOUT")
        )

        with patch(
            "flows.financeiro_medicao.runner._remove_private_file",
            side_effect=OSError(
                "C:\\sensitive\\private.xlsx?token=secret"
            ),
        ):
            payload = self._run_once(collector)

        self.assertEqual(
            payload["error_code"],
            "DOWNLOAD_TEMP_CLEANUP_FAILED",
        )
        self.assertEqual(collector.call_count, 1)
        serialized = json.dumps(payload).casefold()
        self.assertNotIn("sensitive", serialized)
        self.assertNotIn("token", serialized)

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

    def test_event_directory_is_durable_before_journal_publication(self):
        self.runtime_root.mkdir()
        runtime = self.runtime_root / "runtime"
        runtime.mkdir()
        event_id, _ = runner.scheduled_event_identity(self.moment)
        target = runner.event_result_path(self.runtime_root, event_id)
        operations = []
        original_replace = os.replace

        def record_sync(path):
            operations.append(("directory-fsync", Path(path).name))

        def record_file_sync(_descriptor):
            operations.append(("file-fsync",))

        def record_replace(source, destination):
            operations.append(("replace", Path(destination).name))
            original_replace(source, destination)

        with patch.object(
            events,
            "fsync_directory",
            side_effect=record_sync,
        ), patch.object(
            runner.os,
            "fsync",
            side_effect=record_file_sync,
        ), patch.object(
            runner.os,
            "replace",
            side_effect=record_replace,
        ):
            runner._atomic_json_write(target, {"status": "terminal"})

        self.assertEqual(
            operations,
            [
                ("directory-fsync", "events"),
                ("directory-fsync", "runtime"),
                ("file-fsync",),
                ("replace", target.name),
                ("directory-fsync", "events"),
            ],
        )

    def test_event_parent_sync_failure_never_publishes_journal(self):
        self.runtime_root.mkdir()
        runtime = self.runtime_root / "runtime"
        runtime.mkdir()
        event_id, _ = runner.scheduled_event_identity(self.moment)
        target = runner.event_result_path(self.runtime_root, event_id)

        def fail_runtime_sync(path):
            if Path(path) == runtime:
                raise OSError("runtime fsync failed")

        with patch.object(
            events,
            "fsync_directory",
            side_effect=fail_runtime_sync,
        ), patch.object(runner.os, "replace") as replace:
            with self.assertRaisesRegex(OSError, "runtime fsync failed"):
                runner._atomic_json_write(target, {"status": "terminal"})

        replace.assert_not_called()
        self.assertFalse(target.exists())

    def test_event_directory_reparse_is_rejected_before_write(self):
        self.runtime_root.mkdir()
        runtime = self.runtime_root / "runtime"
        events = runtime / "events"
        events.mkdir(parents=True)
        event_id, _ = runner.scheduled_event_identity(self.moment)
        target = runner.event_result_path(
            self.runtime_root,
            event_id,
        )
        original_lstat = Path.lstat

        def report_events_reparse(path):
            metadata = original_lstat(path)
            if Path(path) == events:
                values = list(metadata)
                values[0] = stat.S_IFLNK | 0o777
                return os.stat_result(values)
            return metadata

        with patch.object(
            Path,
            "lstat",
            new=report_events_reparse,
        ), patch.object(runner.os, "replace") as replace:
            with self.assertRaises(ValueError):
                runner._atomic_json_write(
                    target,
                    {"status": "terminal"},
                )

        replace.assert_not_called()

    def test_event_journal_simulated_symlink_is_rejected(self):
        event_id, _ = runner.scheduled_event_identity(self.moment)
        payload = self._terminal_payload()
        journal = self._write_event_journal(event_id, payload)
        original_lstat = Path.lstat

        def report_journal_symlink(path):
            metadata = original_lstat(path)
            if Path(path) == journal:
                values = list(metadata)
                values[0] = stat.S_IFLNK | 0o777
                return os.stat_result(values)
            return metadata

        with patch.object(
            Path,
            "lstat",
            new=report_journal_symlink,
        ), self.assertRaisesRegex(
            ValueError,
            "Invalid event result journal",
        ):
            runner.read_event_result(
                self.runtime_root,
                event_id,
                self.moment,
            )

    def test_quarantine_rejects_simulated_journal_symlink(self):
        event_id, _ = runner.scheduled_event_identity(self.moment)
        journal = self._write_event_journal(
            event_id,
            self._terminal_payload(),
        )
        original_lstat = Path.lstat

        def report_journal_symlink(path):
            metadata = original_lstat(path)
            if Path(path) == journal:
                values = list(metadata)
                values[0] = stat.S_IFLNK | 0o777
                return os.stat_result(values)
            return metadata

        with patch.object(
            Path,
            "lstat",
            new=report_journal_symlink,
        ), patch.object(runner.os, "replace") as replace:
            with self.assertRaises(ValueError):
                runner.quarantine_event_result(
                    self.runtime_root,
                    event_id,
                )

        replace.assert_not_called()

    def test_posix_journal_symlink_is_rejected_when_available(self):
        event_id, _ = runner.scheduled_event_identity(self.moment)
        journal = runner.event_result_path(
            self.runtime_root,
            event_id,
        )
        journal.parent.mkdir(parents=True)
        outside = self.runtime_root / "outside.json"
        outside.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": event_id,
                    "scheduled_for": self.moment.isoformat(),
                    "payload": self._terminal_payload(),
                }
            ),
            encoding="utf-8",
        )
        try:
            journal.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")

        with self.assertRaisesRegex(
            ValueError,
            "Invalid event result journal",
        ):
            runner.read_event_result(
                self.runtime_root,
                event_id,
                self.moment,
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
