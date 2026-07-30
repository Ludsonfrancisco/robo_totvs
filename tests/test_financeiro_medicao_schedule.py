from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from flows.common.locks import file_lock
from flows.financeiro_medicao import schedule
import worker


class FinanceiroMedicaoScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.timezone = ZoneInfo("America/Sao_Paulo")
        self.scheduled_for = datetime(
            2026,
            7,
            30,
            0,
            1,
            tzinfo=self.timezone,
        )
        self.settings = SimpleNamespace(
            runtime_root=self.root,
            schedule_enabled=True,
            schedule_hour=0,
            schedule_minute=1,
            timezone="America/Sao_Paulo",
        )

    def tearDown(self):
        self.temporary.cleanup()

    @property
    def signal(self):
        return self.runtime / "schedule.signal.json"

    @property
    def watermark(self):
        return self.runtime / "schedule-watermark.json"

    def _request(self):
        schedule.request_run(
            self.settings,
            self.scheduled_for,
            now=self.scheduled_for,
        )

    def _claim(self, suffix, claimed_at="2026-07-30T00:01:00-03:00"):
        claim = self.runtime / f".schedule.signal.json.claimed.{suffix}"
        claim.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "scheduled_for": self.scheduled_for.isoformat(),
                    "attempt": 0,
                    "next_attempt_at": self.scheduled_for.isoformat(),
                    "claimed_at": claimed_at,
                }
            ),
            encoding="utf-8",
        )
        return claim

    def test_restart_after_0001_catches_up_unprocessed_day(self):
        now = self.scheduled_for.replace(minute=10)

        result = schedule.next_event_at(now, self.settings)

        self.assertEqual(result, self.scheduled_for)

    def test_success_status_or_watermark_prevents_restart_duplicate(self):
        tomorrow = self.scheduled_for.replace(day=31)
        now = self.scheduled_for.replace(minute=10)
        cases = {
            "done": (
                self.root / "done.json",
                {
                    "success": True,
                    "finished_at": "2026-07-30T00:05:00-03:00",
                },
            ),
            "watermark": (
                self.watermark,
                {
                    "local_date": "2026-07-30",
                    "outcome": "success",
                },
            ),
        }
        for name, (path, payload) in cases.items():
            with self.subTest(name=name):
                path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertEqual(
                    schedule.next_event_at(now, self.settings),
                    tomorrow,
                )
                path.unlink()

    def test_transient_failure_waits_for_backoff_then_retries(self):
        with patch.object(
            schedule.runner,
            "run_once",
            side_effect=[
                {"success": False, "error_code": "LOCKED"},
                {"success": True, "error_code": ""},
            ],
        ) as run_once, patch.object(
            schedule,
            "RETRY_BASE_SECONDS",
            "60",
        ), patch.object(schedule, "RETRY_MAX_SECONDS", "900"):
            self._request()
            first = schedule.run_signal_if_due(
                self.settings,
                now=self.scheduled_for,
            )
            early = schedule.run_signal_if_due(
                self.settings,
                now=self.scheduled_for.replace(second=59),
            )
            retry_payload = json.loads(
                self.signal.read_text(encoding="utf-8")
            )
            retried = schedule.run_signal_if_due(
                self.settings,
                now=self.scheduled_for.replace(minute=2),
            )

        self.assertFalse(first)
        self.assertIsNone(early)
        self.assertTrue(retried)
        self.assertEqual(run_once.call_count, 2)
        self.assertEqual(retry_payload["attempt"], 1)
        self.assertEqual(
            retry_payload["next_attempt_at"],
            "2026-07-30T00:02:00-03:00",
        )
        self.assertFalse(self.signal.exists())
        self.assertEqual(
            json.loads(self.watermark.read_text(encoding="utf-8"))[
                "outcome"
            ],
            "success",
        )

    def test_permanent_failure_finishes_event_without_retry(self):
        with patch.object(
            schedule.runner,
            "run_once",
            return_value={
                "success": False,
                "error_code": "AUTH_EXPIRED",
            },
        ) as run_once:
            self._request()
            result = schedule.run_signal_if_due(
                self.settings,
                now=self.scheduled_for,
            )
            repeated = schedule.run_signal_if_due(
                self.settings,
                now=self.scheduled_for.replace(hour=1),
            )

        watermark = json.loads(
            self.watermark.read_text(encoding="utf-8")
        )
        self.assertFalse(result)
        self.assertIsNone(repeated)
        self.assertEqual(run_once.call_count, 1)
        self.assertFalse(self.signal.exists())
        self.assertEqual(watermark["outcome"], "terminal")
        self.assertEqual(watermark["error_code"], "AUTH_EXPIRED")

    def test_active_claim_is_not_recovered_or_dispatched(self):
        claim = self._claim("a" * 32)
        flow_lock = self.runtime / "financeiro_medicao.lock"
        with file_lock(flow_lock, wait_seconds=0), patch.object(
            schedule.runner,
            "run_once",
        ) as run_once:
            result = schedule.run_signal_if_due(
                self.settings,
                now=self.scheduled_for.replace(minute=10),
            )

        self.assertIsNone(result)
        self.assertTrue(claim.exists())
        run_once.assert_not_called()

    def test_orphaned_claim_is_recovered_after_restart(self):
        claim = self._claim("b" * 32)
        with patch.object(
            schedule.runner,
            "run_once",
            return_value={"success": True, "error_code": ""},
        ) as run_once:
            result = schedule.run_signal_if_due(
                self.settings,
                now=self.scheduled_for.replace(minute=10),
            )

        self.assertTrue(result)
        self.assertFalse(claim.exists())
        run_once.assert_called_once()

    def test_invalid_runner_config_is_terminal_and_sanitized(self):
        secret = "https://user:password@example.invalid/?token=secret"
        self._request()
        with patch.object(
            schedule.runner,
            "run_once",
            side_effect=ValueError(secret),
        ), patch.object(schedule.logger, "warning") as warning:
            result = schedule.run_signal_if_due(
                self.settings,
                now=self.scheduled_for,
            )

        watermark = json.loads(
            self.watermark.read_text(encoding="utf-8")
        )
        logged = " ".join(str(call) for call in warning.call_args_list)
        self.assertFalse(result)
        self.assertFalse(self.signal.exists())
        self.assertEqual(watermark["error_code"], "CONFIG_INVALID")
        self.assertIn("CONFIG_INVALID", logged)
        self.assertNotIn(secret, logged)


class FinanceiroMedicaoWorkerIntegrationTests(unittest.TestCase):
    def test_overdue_event_runs_after_longer_scheduled_job(self):
        current = [datetime(2026, 7, 30, 23, 50)]
        multiplica_at = current[0]
        financeiro_at = datetime(2026, 7, 31, 0, 1)

        def finish_multiplica_after_deadline():
            current[0] = datetime(2026, 7, 31, 0, 2)
            return True

        with patch.object(worker, "_ensure_dirs"), patch.object(
            worker,
            "_scheduled_events",
            return_value=[
                ("multiplica", multiplica_at),
                ("financeiro_medicao", financeiro_at),
            ],
        ) as scheduled_events, patch.object(
            worker,
            "_local_now",
            side_effect=lambda: current[0],
        ), patch.object(
            worker,
            "_run_scheduled_multiplica",
            side_effect=finish_multiplica_after_deadline,
        ), patch.object(
            worker,
            "_run_scheduled_financeiro_medicao",
            side_effect=KeyboardInterrupt(),
        ) as financeiro:
            worker.loop_forever()

        financeiro.assert_called_once_with(scheduled_for=financeiro_at)
        scheduled_events.assert_called_once_with()

    def test_disabled_schedule_ignores_invalid_config_on_import(self):
        environment = os.environ.copy()
        environment.update(
            {
                "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED": "false",
                "FINANCEIRO_MEDICAO_TIMEZONE": "Invalid/Timezone",
                "FINANCEIRO_MEDICAO_SCHEDULE_HOUR": "not-an-hour",
                "FINANCEIRO_MEDICAO_SCHEDULE_MINUTE": "not-a-minute",
            }
        )
        completed = subprocess.run(
            [sys.executable, "-c", "import worker; print('worker-imported')"],
            cwd=Path(worker.__file__).parent,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertIn("worker-imported", completed.stdout)

    def test_invalid_enabled_config_preserves_other_events(self):
        now = datetime(2026, 7, 30, 10, 1)
        with patch.object(
            worker, "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED", True
        ), patch.object(
            worker, "FINANCEIRO_MEDICAO_TIMEZONE", "Invalid/Timezone"
        ), patch.object(
            worker, "FINANCEIRO_MEDICAO_SCHEDULE_HOUR", "not-an-hour"
        ), patch.object(worker, "PROTHEUS_ENABLED", False), patch.object(
            worker, "ROUTERBOX_ENABLED", True
        ), patch.object(
            worker, "MULTIPLICA_SCHEDULE_ENABLED", True
        ), patch.object(worker.logger, "error") as error:
            names = {name for name, _ in worker._scheduled_events(now)}

        self.assertEqual(names, {"routerbox", "multiplica"})
        logged = " ".join(str(call) for call in error.call_args_list)
        self.assertIn("CONFIG_INVALID", logged)
        self.assertNotIn("Invalid/Timezone", logged)

    def test_financeiro_timezone_does_not_change_existing_schedules(self):
        utc = ZoneInfo("UTC")
        protheus_now = datetime(2026, 7, 30, 5, tzinfo=utc)
        other_now = datetime(2026, 7, 30, 10, 1, tzinfo=utc)
        with patch.object(
            worker, "FINANCEIRO_MEDICAO_TIMEZONE", "Asia/Tokyo"
        ), patch.object(worker, "SCHEDULE_HOUR", 6), patch.object(
            worker, "SCHEDULE_MINUTE", 0
        ), patch.object(
            worker, "MULTIPLICA_SCHEDULE_HOUR", 23
        ), patch.object(
            worker, "MULTIPLICA_SCHEDULE_MINUTE", 50
        ), patch.object(worker, "ROUTERBOX_INTERVAL_MIN", 30):
            protheus = worker._next_run_at(protheus_now)
            multiplica = worker._next_multiplica_run_at(other_now)
            routerbox = worker._next_routerbox_run_at(other_now)

        self.assertEqual(
            protheus, datetime(2026, 7, 30, 6, tzinfo=utc)
        )
        self.assertEqual(
            multiplica, datetime(2026, 7, 30, 23, 50, tzinfo=utc)
        )
        self.assertEqual(
            routerbox, datetime(2026, 7, 30, 10, 30, tzinfo=utc)
        )

    def test_worker_uses_financeiro_catch_up_when_enabled(self):
        timezone = ZoneInfo("America/Sao_Paulo")
        now = datetime(2026, 7, 30, 0, 10, tzinfo=timezone)
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            worker, "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED", True
        ), patch.object(
            worker, "FINANCEIRO_MEDICAO_TIMEZONE", "America/Sao_Paulo"
        ), patch.object(
            worker, "FINANCEIRO_MEDICAO_SCHEDULE_HOUR", 0
        ), patch.object(
            worker, "FINANCEIRO_MEDICAO_SCHEDULE_MINUTE", 1
        ), patch.object(
            worker,
            "FINANCEIRO_MEDICAO_RUNTIME_ROOT",
            Path(temporary) / "financeiro_medicao",
            create=True,
        ), patch.object(worker, "PROTHEUS_ENABLED", False), patch.object(
            worker, "ROUTERBOX_ENABLED", False
        ), patch.object(worker, "MULTIPLICA_SCHEDULE_ENABLED", False):
            events = dict(worker._scheduled_events(now))

        self.assertEqual(
            events["financeiro_medicao"],
            datetime(2026, 7, 30, 0, 1, tzinfo=timezone),
        )
