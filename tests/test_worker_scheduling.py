from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import worker
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
