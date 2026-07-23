from datetime import datetime
import unittest
from unittest.mock import patch

import worker


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
