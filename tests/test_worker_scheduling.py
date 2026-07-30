from datetime import datetime
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import worker
from flows.common.locks import LockUnavailable
from flows.multiplica import runner as multiplica_runner


class WorkerSchedulingTests(unittest.TestCase):
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
