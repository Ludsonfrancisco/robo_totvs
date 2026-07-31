from datetime import datetime
from contextlib import contextmanager
import builtins
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import worker
from flows.common.locks import LockUnavailable
from flows.financeiro_medicao.config import Settings as FinanceiroMedicaoSettings
from flows.financeiro_medicao import runner as financeiro_medicao_runner
from flows.financeiro_medicao import schedule as financeiro_medicao_schedule
from flows.multiplica import runner as multiplica_runner


class WorkerSchedulingTests(unittest.TestCase):
    def _financeiro_settings(self, runtime_root):
        return SimpleNamespace(
            runtime_root=Path(runtime_root),
            schedule_enabled=True,
            schedule_hour=0,
            schedule_minute=1,
            timezone="America/Sao_Paulo",
        )

    def test_financeiro_medicao_disabled_by_default(self):
        self.assertFalse(worker.FINANCEIRO_MEDICAO_SCHEDULE_ENABLED)

    def test_next_financeiro_medicao_run_is_0001_after_day_rollover(self):
        now = datetime(2026, 7, 30, 23, 59, 30)
        self.assertEqual(
            worker._next_financeiro_medicao_run_at(now),
            datetime(2026, 7, 31, 0, 1),
        )

    def test_runtime_scheduler_converts_utc_to_sao_paulo(self):
        with patch.object(
            worker,
            "FINANCEIRO_MEDICAO_TIMEZONE",
            "America/Sao_Paulo",
        ):
            now = datetime(
                2026,
                7,
                30,
                23,
                59,
                30,
                tzinfo=ZoneInfo("UTC"),
            )
            result = worker._next_financeiro_medicao_run_at(now)

        self.assertEqual(
            result.isoformat(),
            "2026-07-31T00:01:00-03:00",
        )

    def test_financeiro_medicao_enters_scheduler_only_when_enabled(self):
        now = datetime(2026, 7, 30, 0, 0)
        with patch.object(
            worker,
            "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED",
            False,
        ):
            disabled_names = {
                name for name, _ in worker._scheduled_events(now)
            }
        with patch.object(
            worker,
            "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED",
            True,
        ), patch.object(
            worker,
            "_financeiro_schedule_settings",
            return_value=self._financeiro_settings(
                "/app/data_pipeline/financeiro_medicao"
            ),
        ):
            enabled_events = dict(worker._scheduled_events(now))

        self.assertNotIn("financeiro_medicao", disabled_names)
        self.assertEqual(
            enabled_events["financeiro_medicao"],
            datetime(2026, 7, 30, 0, 1),
        )

    def test_sleep_helper_compares_timezone_aware_datetimes(self):
        target = datetime(
            2026,
            7,
            30,
            0,
            1,
            tzinfo=ZoneInfo("America/Sao_Paulo"),
        )
        with patch.object(
            worker,
            "_local_now",
            return_value=target,
        ):
            result = worker._sleep_until_or_signal(target)

        self.assertIsNone(result)

    def test_sleep_helper_preserves_naive_datetime_callers(self):
        target = datetime(2026, 7, 30, 0, 1)
        aware_target = target.replace(
            tzinfo=ZoneInfo("America/Sao_Paulo"),
        )
        with patch.object(
            worker,
            "_local_now",
            return_value=aware_target,
        ):
            result = worker._sleep_until_or_signal(target)

        self.assertIsNone(result)

    def test_financeiro_medicao_dispatches_run_once_without_nested_lock(self):
        timezone = ZoneInfo("America/Sao_Paulo")
        scheduled_for = datetime(
            2026,
            7,
            30,
            0,
            1,
            tzinfo=timezone,
        )
        locked_paths = []

        @contextmanager
        def recording_lock(path, *, wait_seconds):
            locked_paths.append(Path(path))
            yield

        with tempfile.TemporaryDirectory() as temporary:
            settings = self._financeiro_settings(temporary)
            Path(temporary, "runtime").mkdir()
            with patch.object(
                worker,
                "_financeiro_schedule_settings",
                return_value=settings,
            ), patch.object(
                worker,
                "_local_now",
                return_value=scheduled_for,
            ), patch.object(
                financeiro_medicao_runner,
                "run_once",
                return_value={"success": True, "error_code": ""},
            ) as run_once, patch.object(
                worker,
                "file_lock",
                side_effect=recording_lock,
            ):
                result = worker._run_scheduled_financeiro_medicao(
                    scheduled_for=scheduled_for,
                )

        self.assertTrue(result)
        event_id, _ = financeiro_medicao_runner.scheduled_event_identity(
            scheduled_for
        )
        run_once.assert_called_once_with(
            settings=settings,
            day=scheduled_for.date(),
            scheduled_for=scheduled_for,
            event_id=event_id,
        )
        self.assertNotIn(worker.GLOBAL_CHROMIUM_LOCK, locked_paths)

    def test_scheduled_financeiro_uses_complete_runtime_settings(self):
        timezone = ZoneInfo("America/Sao_Paulo")
        scheduled_for = datetime(
            2026,
            7,
            30,
            0,
            1,
            tzinfo=timezone,
        )
        with tempfile.TemporaryDirectory() as temporary:
            runtime_root = Path(temporary, "financeiro_medicao")
            runtime_root.mkdir()
            environment = {
                "FINANCEIRO_MEDICAO_LOGA_URL": (
                    "https://dashboard.loga.net.br/medicao_pagamento"
                ),
                "FINANCEIRO_MEDICAO_RUNTIME_ROOT": str(runtime_root),
                "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED": "true",
                "FINANCEIRO_MEDICAO_SCHEDULE_HOUR": "0",
                "FINANCEIRO_MEDICAO_SCHEDULE_MINUTE": "1",
                "FINANCEIRO_MEDICAO_TIMEZONE": "America/Sao_Paulo",
                "FINANCEIRO_MEDICAO_LOCK_WAIT_SECONDS": "1200",
                "LOGA_DASHBOARD_USER": "financeiro-user",
                "LOGA_DASHBOARD_PASSWORD": "financeiro-password",
            }
            with patch.dict(os.environ, environment, clear=True), patch.object(
                worker,
                "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED",
                True,
            ), patch.object(
                worker,
                "FINANCEIRO_MEDICAO_RUNTIME_ROOT",
                str(runtime_root),
            ), patch.object(
                financeiro_medicao_schedule,
                "request_run",
            ) as request_run, patch.object(
                financeiro_medicao_schedule,
                "run_signal_if_due",
                return_value=True,
            ):
                result = worker._run_scheduled_financeiro_medicao(
                    scheduled_for=scheduled_for,
                )

        self.assertTrue(result)
        settings = request_run.call_args.args[0]
        self.assertIsInstance(settings, FinanceiroMedicaoSettings)
        self.assertEqual(settings.username, "financeiro-user")
        self.assertEqual(settings.password, "financeiro-password")
        self.assertEqual(settings.lock_wait_seconds, 1200)

    def test_financeiro_medicao_locked_result_is_sanitized_and_nonfatal(self):
        timezone = ZoneInfo("America/Sao_Paulo")
        scheduled_for = datetime(
            2026,
            7,
            30,
            0,
            1,
            tzinfo=timezone,
        )
        with tempfile.TemporaryDirectory() as temporary:
            settings = self._financeiro_settings(temporary)
            Path(temporary, "runtime").mkdir()
            with patch.object(
                worker,
                "_financeiro_schedule_settings",
                return_value=settings,
            ), patch.object(
                worker,
                "_local_now",
                return_value=scheduled_for,
            ), patch.object(
                financeiro_medicao_runner,
                "run_once",
                return_value={
                    "success": False,
                    "error_code": "LOCKED",
                    "private": "secret-token",
                },
            ), patch.object(worker.logger, "warning") as warning:
                result = worker._run_scheduled_financeiro_medicao(
                    scheduled_for=scheduled_for,
                )

        self.assertFalse(result)
        logged = " ".join(str(call) for call in warning.call_args_list)
        self.assertIn("LOCKED", logged)
        self.assertNotIn("secret-token", logged)

    def test_financeiro_medicao_exception_is_sanitized_and_nonfatal(self):
        secret = "https://user:password@example.invalid/?token=secret"
        timezone = ZoneInfo("America/Sao_Paulo")
        scheduled_for = datetime(
            2026,
            7,
            30,
            0,
            1,
            tzinfo=timezone,
        )
        with tempfile.TemporaryDirectory() as temporary:
            settings = self._financeiro_settings(temporary)
            Path(temporary, "runtime").mkdir()
            with patch.object(
                worker,
                "_financeiro_schedule_settings",
                return_value=settings,
            ), patch.object(
                worker,
                "_local_now",
                return_value=scheduled_for,
            ), patch.object(
                financeiro_medicao_runner,
                "run_once",
                side_effect=RuntimeError(secret),
            ), patch.object(worker.logger, "warning") as warning:
                result = worker._run_scheduled_financeiro_medicao(
                    scheduled_for=scheduled_for,
                )

        self.assertFalse(result)
        logged = " ".join(str(call) for call in warning.call_args_list)
        self.assertIn("UNEXPECTED_ERROR", logged)
        self.assertNotIn(secret, logged)

    def test_loop_dispatches_financeiro_medicao_event_exactly_once(self):
        target = datetime(2026, 7, 30, 0, 1)
        with patch.object(worker, "_ensure_dirs"), patch.object(
            worker,
            "_scheduled_events",
            side_effect=[
                [("financeiro_medicao", target)],
                KeyboardInterrupt(),
            ],
        ), patch.object(
            worker,
            "_local_now",
            return_value=target,
        ), patch.object(
            worker,
            "_run_scheduled_financeiro_medicao",
            return_value=True,
        ) as dispatch, patch.object(
            worker,
            "_advance_scheduled_event",
            side_effect=KeyboardInterrupt(),
        ):
            worker.loop_forever()

        dispatch.assert_called_once_with(scheduled_for=target)

    def test_protheus_disabled_keeps_only_routerbox(self):
        now = datetime(2026, 7, 23, 12, 0)
        with patch.object(worker, "PROTHEUS_ENABLED", False), patch.object(
            worker, "ROUTERBOX_ENABLED", True
        ):
            names = [name for name, _ in worker._scheduled_events(now)]
        self.assertEqual(names, ["routerbox"])

    def test_protheus_enabled_is_still_available_for_rollback(self):
        now = datetime(2026, 7, 23, 5, 0)
        with patch.object(worker, "PROTHEUS_ENABLED", True), patch.object(
            worker, "ROUTERBOX_ENABLED", True
        ):
            names = {name for name, _ in worker._scheduled_events(now)}
        self.assertEqual(names, {"protheus", "routerbox"})

    def test_routerbox_schedule_contract_is_unchanged(self):
        now = datetime(2026, 7, 23, 10, 1)
        with patch.object(worker, "ROUTERBOX_INTERVAL_MIN", 30):
            result = worker._next_routerbox_run_at(now)
        self.assertEqual(result, datetime(2026, 7, 23, 10, 30))

    def test_multiplica_disabled_never_enters_scheduler(self):
        now = datetime(2026, 7, 23, 23, 40)
        with patch.object(worker, "MULTIPLICA_SCHEDULE_ENABLED", False):
            names = [name for name, _ in worker._scheduled_events(now)]
        self.assertNotIn("multiplica", names)

    def test_multiplica_enabled_uses_2350(self):
        now = datetime(2026, 7, 23, 23, 40)
        with patch.object(
            worker, "MULTIPLICA_SCHEDULE_ENABLED", True
        ), patch.object(worker, "MULTIPLICA_SCHEDULE_HOUR", 23), patch.object(
            worker, "MULTIPLICA_SCHEDULE_MINUTE", 50
        ):
            events = dict(worker._scheduled_events(now))
        self.assertEqual(events["multiplica"], datetime(2026, 7, 23, 23, 50))

    def test_multiplica_manual_signal_runs_with_scheduler_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal_file = Path(tmp) / "multiplica.signal"
            signal_file.touch()
            with patch.object(
                worker, "MULTIPLICA_SCHEDULE_ENABLED", False
            ), patch.object(
                worker, "MULTIPLICA_SIGNAL_FILE", signal_file
            ), patch.object(
                multiplica_runner, "run_once"
            ) as run_once:
                consumed = worker._run_multiplica_signal_if_present()
                self.assertFalse(signal_file.exists())

        self.assertTrue(consumed)
        run_once.assert_called_once_with()

    def test_multiplica_dispatch_uses_only_signal_claim_lock(self):
        locked_paths = []

        @contextmanager
        def recording_lock(path, *, wait_seconds):
            locked_paths.append((Path(path), wait_seconds))
            yield

        with tempfile.TemporaryDirectory() as tmp:
            signal_file = Path(tmp) / "multiplica.signal"
            signal_file.touch()
            with patch.object(
                worker, "MULTIPLICA_SIGNAL_FILE", signal_file
            ), patch.object(
                multiplica_runner, "run_once"
            ) as run_once, patch.object(
                worker, "file_lock", side_effect=recording_lock
            ):
                worker._run_multiplica_signal_if_present()

        run_once.assert_called_once_with()
        self.assertEqual(
            locked_paths,
            [
                (
                    signal_file.parent
                    / ".multiplica.signal.claim.lock",
                    0,
                )
            ],
        )
        self.assertNotEqual(locked_paths[0][0], worker.GLOBAL_CHROMIUM_LOCK)

    def test_multiplica_signal_is_preserved_when_global_lock_is_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal_file = Path(tmp) / "multiplica.signal"
            signal_file.touch()
            with patch.object(
                worker, "MULTIPLICA_SIGNAL_FILE", signal_file
            ), patch.object(
                multiplica_runner,
                "run_once",
                side_effect=multiplica_runner.AlreadyRunning("busy"),
            ), patch.object(
                worker, "file_lock"
            ) as worker_lock:
                consumed = worker._run_multiplica_signal_if_present()

            self.assertFalse(consumed)
            self.assertTrue(signal_file.exists())
            self.assertEqual(
                list(signal_file.parent.glob("*.claimed.*")),
                [],
            )
        worker_lock.assert_called_once_with(
            signal_file.parent / ".multiplica.signal.claim.lock",
            wait_seconds=0,
        )

    def test_active_multiplica_claim_is_not_recovered_by_other_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signal_file = root / "multiplica.signal"
            active_claim = root / (
                ".multiplica.signal.claimed."
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            )
            active_claim.touch()
            with patch.object(
                worker, "MULTIPLICA_SIGNAL_FILE", signal_file
            ), patch.object(
                worker,
                "file_lock",
                side_effect=LockUnavailable("LOCKED"),
            ), patch.object(
                multiplica_runner, "run_once"
            ) as run_once:
                consumed = worker._run_multiplica_signal_if_present()

            self.assertFalse(consumed)
            self.assertTrue(active_claim.exists())
            self.assertFalse(signal_file.exists())
            run_once.assert_not_called()

    def test_orphaned_multiplica_claim_is_recovered_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signal_file = root / "multiplica.signal"
            orphan = root / (
                ".multiplica.signal.claimed."
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            )
            unrelated = root / ".multiplica.signal.claimed.not-ours"
            orphan.touch()
            unrelated.touch()
            with patch.object(
                worker, "MULTIPLICA_SIGNAL_FILE", signal_file
            ), patch.object(
                multiplica_runner,
                "run_once",
                side_effect=multiplica_runner.AlreadyRunning("busy"),
            ):
                consumed = worker._run_multiplica_signal_if_present()

            self.assertFalse(consumed)
            self.assertTrue(signal_file.exists())
            self.assertFalse(orphan.exists())
            self.assertTrue(unrelated.exists())

    def test_orphaned_claim_is_removed_when_signal_already_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signal_file = root / "multiplica.signal"
            orphan = root / (
                ".multiplica.signal.claimed."
                "cccccccccccccccccccccccccccccccc"
            )
            unrelated = root / ".multiplica.signal.claimed.not-ours"
            signal_file.touch()
            orphan.touch()
            unrelated.touch()
            with patch.object(
                worker, "MULTIPLICA_SIGNAL_FILE", signal_file
            ), patch.object(
                multiplica_runner, "run_once"
            ) as run_once:
                first = worker._run_multiplica_signal_if_present()
                second = worker._run_multiplica_signal_if_present()

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertFalse(signal_file.exists())
            self.assertFalse(orphan.exists())
            self.assertTrue(unrelated.exists())
            run_once.assert_called_once_with()

    def test_scheduled_multiplica_collision_creates_retry_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal_file = Path(tmp) / "multiplica.signal"
            with patch.object(
                worker, "MULTIPLICA_SIGNAL_FILE", signal_file
            ), patch.object(
                multiplica_runner,
                "run_once",
                side_effect=multiplica_runner.AlreadyRunning("busy"),
            ):
                completed = worker._run_scheduled_multiplica()

            self.assertFalse(completed)
            self.assertTrue(signal_file.exists())

    def test_protheus_browser_entrypoint_uses_global_lock(self):
        events = []

        @contextmanager
        def recording_lock(path, *, wait_seconds):
            events.append(("lock-enter", Path(path), wait_seconds))
            try:
                yield
            finally:
                events.append(("lock-exit", Path(path), wait_seconds))

        def robot_main(_argv):
            events.append(("protheus",))
            return 0

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            worker, "LOG_FILE", Path(temporary) / "run.log"
        ), patch.object(
            worker, "file_lock", side_effect=recording_lock
        ), patch(
            "main.main", side_effect=robot_main
        ):
            worker._executar_robo("scheduled")

        self.assertEqual(
            events,
            [
                (
                    "lock-enter",
                    worker.GLOBAL_CHROMIUM_LOCK,
                    worker.CHROMIUM_LOCK_WAIT_SECONDS,
                ),
                ("protheus",),
                (
                    "lock-exit",
                    worker.GLOBAL_CHROMIUM_LOCK,
                    worker.CHROMIUM_LOCK_WAIT_SECONDS,
                ),
            ],
        )

    def test_protheus_lock_contention_propagates_typed_locked(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            worker, "LOG_FILE", Path(temporary) / "run.log"
        ), patch.object(
            worker,
            "file_lock",
            side_effect=LockUnavailable("LOCKED"),
        ), patch("main.main") as robot_main:
            with self.assertRaises(LockUnavailable):
                worker._executar_robo("full")

        robot_main.assert_not_called()

    def test_protheus_lock_contention_requeues_without_auto_retry_delay(self):
        with tempfile.TemporaryDirectory() as temporary:
            signal_file = Path(temporary) / "run.signal"
            with patch.object(
                worker, "SIGNAL_FILE", signal_file
            ), patch.object(
                worker,
                "_run_once",
                side_effect=LockUnavailable("LOCKED"),
            ), patch.object(worker.time, "sleep") as sleep:
                worker._run_with_auto_retry("retry-falhos")

            self.assertEqual(
                json.loads(signal_file.read_text(encoding="utf-8")),
                {"mode": "retry-falhos"},
            )
            sleep.assert_called_once_with(worker.POLL_INTERVAL_S)

    def test_protheus_requeue_preserves_stronger_concurrent_signal(self):
        with tempfile.TemporaryDirectory() as temporary:
            signal_file = Path(temporary) / "run.signal"
            original = '{"mode":"full","request":"concurrent"}\n'
            signal_file.write_text(original, encoding="utf-8")
            with patch.object(worker, "SIGNAL_FILE", signal_file):
                worker._request_protheus_retry("retry-falhos")

            self.assertEqual(
                signal_file.read_text(encoding="utf-8"),
                original,
            )

    def test_protheus_requeue_upgrades_retry_signal_to_full(self):
        with tempfile.TemporaryDirectory() as temporary:
            signal_file = Path(temporary) / "run.signal"
            signal_file.write_text(
                '{"mode":"retry-falhos"}\n',
                encoding="utf-8",
            )
            with patch.object(worker, "SIGNAL_FILE", signal_file):
                worker._request_protheus_retry("scheduled")

            self.assertEqual(
                json.loads(signal_file.read_text(encoding="utf-8")),
                {"mode": "full"},
            )

    def test_protheus_claim_ack_preserves_concurrent_producer_signal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            signal_file = root / "run.signal"
            signal_file.write_text(
                '{"mode":"retry-falhos"}\n',
                encoding="utf-8",
            )

            def publish_concurrent_signal(_mode):
                signal_file.write_text(
                    '{"mode":"full","request":"concurrent"}\n',
                    encoding="utf-8",
                )

            with patch.object(
                worker, "SIGNAL_FILE", signal_file
            ), patch.object(
                worker,
                "_run_with_auto_retry",
                side_effect=publish_concurrent_signal,
            ):
                consumed = worker._run_protheus_signal_if_present()

            self.assertTrue(consumed)
            self.assertEqual(
                json.loads(signal_file.read_text(encoding="utf-8")),
                {"mode": "full", "request": "concurrent"},
            )
            self.assertEqual(
                list(root.glob(".run.signal.claimed.*")),
                [],
            )

    def test_locked_protheus_claim_merges_with_concurrent_full_signal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            signal_file = root / "run.signal"
            signal_file.write_text(
                '{"mode":"retry-falhos"}\n',
                encoding="utf-8",
            )

            def contend_after_concurrent_publish(*, mode):
                self.assertEqual(mode, "retry-falhos")
                signal_file.write_text(
                    '{"mode":"full","request":"concurrent"}\n',
                    encoding="utf-8",
                )
                raise LockUnavailable("LOCKED")

            with patch.object(
                worker, "SIGNAL_FILE", signal_file
            ), patch.object(
                worker,
                "_run_once",
                side_effect=contend_after_concurrent_publish,
            ), patch.object(worker.time, "sleep"):
                consumed = worker._run_protheus_signal_if_present()

            self.assertTrue(consumed)
            self.assertEqual(
                json.loads(signal_file.read_text(encoding="utf-8")),
                {"mode": "full", "request": "concurrent"},
            )
            self.assertEqual(
                list(root.glob(".run.signal.claimed.*")),
                [],
            )

    def test_protheus_claim_is_restored_when_dispatch_raises(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            signal_file = root / "run.signal"
            signal_file.write_text(
                '{"mode":"full"}\n',
                encoding="utf-8",
            )

            with patch.object(
                worker, "SIGNAL_FILE", signal_file
            ), patch.object(
                worker,
                "_run_with_auto_retry",
                side_effect=RuntimeError("transient worker failure"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "transient worker failure",
                ):
                    worker._run_protheus_signal_if_present()

            self.assertEqual(
                json.loads(signal_file.read_text(encoding="utf-8")),
                {"mode": "full"},
            )
            self.assertEqual(
                list(root.glob(".run.signal.claimed.*")),
                [],
            )

    def test_orphaned_protheus_claim_is_recovered_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            signal_file = root / "run.signal"
            orphan = root / (
                ".run.signal.claimed."
                "dddddddddddddddddddddddddddddddd"
            )
            unrelated = root / ".run.signal.claimed.not-ours"
            orphan.write_text(
                '{"mode":"retry-falhos"}\n',
                encoding="utf-8",
            )
            unrelated.touch()

            with patch.object(
                worker, "SIGNAL_FILE", signal_file
            ), patch.object(
                worker, "_run_with_auto_retry"
            ) as run:
                consumed = worker._run_protheus_signal_if_present()

            self.assertTrue(consumed)
            run.assert_called_once_with("retry-falhos")
            self.assertFalse(orphan.exists())
            self.assertFalse(signal_file.exists())
            self.assertTrue(unrelated.exists())

    def test_active_protheus_claim_is_not_recovered_by_other_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            signal_file = root / "run.signal"
            active_claim = root / (
                ".run.signal.claimed."
                "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
            )
            active_claim.write_text(
                '{"mode":"full"}\n',
                encoding="utf-8",
            )

            with patch.object(
                worker, "SIGNAL_FILE", signal_file
            ), patch.object(
                worker,
                "file_lock",
                side_effect=LockUnavailable("LOCKED"),
            ), patch.object(
                worker, "_run_with_auto_retry"
            ) as run:
                consumed = worker._run_protheus_signal_if_present()

            self.assertFalse(consumed)
            self.assertTrue(active_claim.exists())
            self.assertFalse(signal_file.exists())
            run.assert_not_called()

    def test_lock_contention_preserves_previous_run_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log_file = root / "run.log"
            done_file = root / "run.done"
            ready_file = root / "signal.ready"
            log_file.write_text("previous log", encoding="utf-8")
            done_file.write_text("previous done", encoding="utf-8")
            ready_file.write_text("previous ready", encoding="utf-8")
            imported = []
            original_import = builtins.__import__

            def recording_import(name, *args, **kwargs):
                imported.append(name)
                return original_import(name, *args, **kwargs)

            with patch.object(
                worker, "LOG_FILE", log_file
            ), patch.object(
                worker, "DONE_FILE", done_file
            ), patch.object(
                worker, "READY_FILE", ready_file
            ), patch.object(
                worker,
                "file_lock",
                side_effect=LockUnavailable("LOCKED"),
            ), patch(
                "builtins.__import__",
                side_effect=recording_import,
            ):
                with self.assertRaises(LockUnavailable):
                    worker._run_once("full")

            self.assertEqual(
                log_file.read_text(encoding="utf-8"),
                "previous log",
            )
            self.assertEqual(
                done_file.read_text(encoding="utf-8"),
                "previous done",
            )
            self.assertEqual(
                ready_file.read_text(encoding="utf-8"),
                "previous ready",
            )
            self.assertNotIn("main", imported)

    def test_import_failure_cleans_old_artifacts_after_acquiring_lock(self):
        events = []
        original_import = builtins.__import__

        @contextmanager
        def recording_lock(_path, *, wait_seconds):
            events.append(("lock-enter", wait_seconds))
            try:
                yield
            finally:
                events.append(("lock-exit", wait_seconds))

        def failing_import(name, *args, **kwargs):
            if name == "main":
                events.append(("import",))
                raise ImportError("protheus entrypoint unavailable")
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log_file = root / "run.log"
            done_file = root / "run.done"
            ready_file = root / "signal.ready"
            log_file.write_text("previous log", encoding="utf-8")
            done_file.write_text("previous done", encoding="utf-8")
            ready_file.write_text("previous ready", encoding="utf-8")

            with patch.object(
                worker, "LOG_FILE", log_file
            ), patch.object(
                worker, "DONE_FILE", done_file
            ), patch.object(
                worker, "READY_FILE", ready_file
            ), patch.object(
                worker, "file_lock", side_effect=recording_lock
            ), patch(
                "builtins.__import__",
                side_effect=failing_import,
            ), patch.object(
                worker,
                "_read_checkpoint_summary",
                return_value=(10, 10, []),
            ) as checkpoint_summary:
                worker._run_once("full")

            payload = json.loads(
                done_file.read_text(encoding="utf-8")
            )
            self.assertFalse(payload["success"])
            self.assertEqual(payload["tecnicos_total"], 0)
            self.assertEqual(payload["tecnicos_ok"], 0)
            self.assertFalse(ready_file.exists())
            checkpoint_summary.assert_not_called()
            self.assertNotIn(
                "previous log",
                log_file.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                events[:2],
                [
                    (
                        "lock-enter",
                        worker.CHROMIUM_LOCK_WAIT_SECONDS,
                    ),
                    ("import",),
                ],
            )

    def test_acquired_lock_cleans_artifacts_before_robot_execution(self):
        events = []

        @contextmanager
        def recording_lock(_path, *, wait_seconds):
            events.append(("lock-enter", wait_seconds))
            try:
                yield
            finally:
                events.append(("lock-exit", wait_seconds))

        def record_cleanup():
            events.append(("cleanup",))

        def robot_main(_argv):
            events.append(("robot",))
            return 0

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            worker, "LOG_FILE", Path(temporary) / "run.log"
        ), patch.object(
            worker, "file_lock", side_effect=recording_lock
        ), patch.object(
            worker,
            "_cleanup_run_artifacts",
            side_effect=record_cleanup,
        ), patch(
            "main.main", side_effect=robot_main
        ):
            worker._run_once("full")

        self.assertEqual(
            events[:3],
            [
                ("lock-enter", worker.CHROMIUM_LOCK_WAIT_SECONDS),
                ("cleanup",),
                ("robot",),
            ],
        )

    def test_routerbox_browser_entrypoint_uses_global_lock(self):
        events = []

        @contextmanager
        def recording_lock(path, *, wait_seconds):
            events.append(("lock-enter", Path(path), wait_seconds))
            try:
                yield
            finally:
                events.append(("lock-exit", Path(path), wait_seconds))

        def routerbox_main():
            events.append(("routerbox",))
            return 0

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(
                worker, "ROUTERBOX_DIR", root
            ), patch.object(
                worker, "ROUTERBOX_DONE_FILE", root / "done.json"
            ), patch.object(
                worker, "file_lock", side_effect=recording_lock
            ), patch(
                "flows.routerbox_backlog.run_routerbox_backlog",
                side_effect=routerbox_main,
            ):
                worker._run_routerbox_backlog()

        self.assertEqual(
            events,
            [
                (
                    "lock-enter",
                    worker.GLOBAL_CHROMIUM_LOCK,
                    worker.CHROMIUM_LOCK_WAIT_SECONDS,
                ),
                ("routerbox",),
                (
                    "lock-exit",
                    worker.GLOBAL_CHROMIUM_LOCK,
                    worker.CHROMIUM_LOCK_WAIT_SECONDS,
                ),
            ],
        )
