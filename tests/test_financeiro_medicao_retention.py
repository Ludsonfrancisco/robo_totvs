import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flows.financeiro_medicao import runner
from flows.financeiro_medicao import retention
from flows.financeiro_medicao.retention import cleanup


class FinanceiroMedicaoRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "financeiro_medicao"
        for name in ("runtime", "inbox", "quarantine", "published"):
            (self.root / name).mkdir(parents=True)
        self.now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)

    def _old(self, path, *, days):
        timestamp = (self.now - timedelta(days=days)).timestamp()
        os.utime(path, (timestamp, timestamp))
        return path

    def _file(self, relative, *, days):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        return self._old(path, days=days)

    def test_runtime_removes_only_old_temporary_files(self):
        removable = self._file("runtime/.download.tmp", days=2)
        recent = self._file("runtime/.recent.tmp", days=1)
        durable = self._file("runtime/events/event.result.json", days=20)
        outside = self._file("published/.outside.tmp", days=20)

        cleanup(self.root.resolve(), current_references=(), now=self.now)

        self.assertFalse(removable.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(durable.exists())
        self.assertTrue(outside.exists())

    def test_removes_only_processed_inbox_bundles_older_than_45_days(self):
        old = self.root / "inbox" / ("a" * 32)
        recent = self.root / "inbox" / ("b" * 32)
        pending = self.root / "inbox" / ("c" * 32)
        for bundle in (old, recent, pending):
            bundle.mkdir()
            self._file(bundle.relative_to(self.root) / "manifest.json", days=60)
        self._file(old.relative_to(self.root) / "processed.json", days=46)
        self._file(recent.relative_to(self.root) / "processed.json", days=45)

        cleanup(self.root.resolve(), current_references=(), now=self.now)

        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(pending.exists())

    def test_removes_old_quarantine_and_runtime_logs_and_evidence(self):
        quarantine = self.root / "quarantine" / ("d" * 32)
        quarantine.mkdir()
        self._file(quarantine.relative_to(self.root) / "quarantine.json", days=31)
        self._old(quarantine, days=31)
        old_log = self._file("runtime/logs/old.log", days=15)
        old_evidence = self._file("runtime/evidence/old.json", days=15)
        recent_log = self._file("runtime/logs/recent.log", days=14)

        cleanup(self.root.resolve(), current_references=(), now=self.now)

        self.assertFalse(quarantine.exists())
        self.assertFalse(old_log.exists())
        self.assertFalse(old_evidence.exists())
        self.assertTrue(recent_log.exists())

    def test_preserves_active_references_and_refuses_paths_outside_root(self):
        protected = self.root / "inbox" / ("e" * 32)
        protected.mkdir()
        self._file(protected.relative_to(self.root) / "processed.json", days=60)
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()

        cleanup(
            self.root.resolve(),
            current_references=(protected.resolve(),),
            now=self.now,
        )
        self.assertTrue(protected.exists())

        with self.assertRaises(ValueError):
            cleanup(
                self.root.resolve(),
                current_references=(outside.resolve(),),
                now=self.now,
            )

    def test_never_follows_symlink_or_simulated_reparse_point(self):
        outside = Path(self.temporary.name) / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        link = self.root / "runtime" / ".escape.tmp"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")
        self._old(link, days=2)

        cleanup(self.root.resolve(), current_references=(), now=self.now)

        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_simulated_windows_reparse_point_is_never_deleted(self):
        target = self._file("runtime/.reparse.tmp", days=2)
        real_metadata = retention._metadata

        def simulated_metadata(path):
            metadata = real_metadata(path)
            if Path(path) != target or metadata is None:
                return metadata

            class ReparseMetadata:
                st_mode = metadata.st_mode
                st_mtime = metadata.st_mtime
                st_nlink = metadata.st_nlink
                st_file_attributes = 0x400

            return ReparseMetadata()

        with patch.object(
            retention,
            "_metadata",
            side_effect=simulated_metadata,
        ):
            cleanup(
                self.root.resolve(),
                current_references=(),
                now=self.now,
            )

        self.assertTrue(target.exists())

    def test_runner_retention_crash_does_not_change_published_result(self):
        payload = {
            "success": True,
            "error_code": "",
            "run_id": "a" * 32,
        }

        with patch.object(
            runner,
            "cleanup_retention",
            side_effect=OSError("cleanup crash"),
            create=True,
        ):
            result = runner._finish_with_retention(
                payload,
                self.root.resolve(),
                now=self.now,
                current_references=(),
            )

        self.assertEqual(result, payload)

    def _settings(self):
        return SimpleNamespace(
            runtime_root=self.root,
            lock_wait_seconds=0,
            timezone="America/Sao_Paulo",
        )

    def _event_payload(self, run_id):
        return {
            "success": True,
            "error_code": "",
            "run_id": run_id,
            "cycle_id": "2026-07-11--2026-08-10",
            "mode": "current",
            "started_at": self.now.isoformat(),
            "finished_at": self.now.isoformat(),
            "next_scheduled_for": None,
        }

    def test_existing_success_repairs_retention_after_previous_crash(self):
        event_id, run_id = runner.scheduled_event_identity(self.now)
        payload = self._event_payload(run_id)

        with patch.object(
            runner,
            "read_event_result",
            return_value=payload,
        ), patch.object(runner, "cleanup_retention") as cleanup_spy:
            result = runner.run_once(
                settings=self._settings(),
                scheduled_for=self.now,
                event_id=event_id,
                clock=lambda: self.now,
            )

        self.assertEqual(result, payload)
        cleanup_spy.assert_called_once()
        references = set(
            cleanup_spy.call_args.kwargs["current_references"]
        )
        self.assertIn(self.root / "inbox" / run_id, references)
        self.assertIn(
            self.root / "runtime" / "proofs" / run_id,
            references,
        )

    def test_reconciled_success_repairs_retention_after_previous_crash(self):
        event_id, run_id = runner.scheduled_event_identity(self.now)
        payload = self._event_payload(run_id)

        with patch.object(
            runner,
            "read_event_result",
            return_value=None,
        ), patch.object(
            runner,
            "_reconcile_published_event_locked",
            return_value=payload,
        ), patch.object(runner, "cleanup_retention") as cleanup_spy:
            result = runner.run_once(
                settings=self._settings(),
                scheduled_for=self.now,
                event_id=event_id,
                clock=lambda: self.now,
            )

        self.assertEqual(result, payload)
        cleanup_spy.assert_called_once()
        references = set(
            cleanup_spy.call_args.kwargs["current_references"]
        )
        self.assertIn(
            runner.event_result_path(self.root, event_id),
            references,
        )
        self.assertIn(
            runner.event_receipt_path(self.root, event_id),
            references,
        )


if __name__ == "__main__":
    unittest.main()
