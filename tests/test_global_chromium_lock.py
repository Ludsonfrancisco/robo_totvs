import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from flows.common.locks import LockUnavailable, file_lock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class GlobalChromiumLockTests(unittest.TestCase):
    @staticmethod
    def _environment():
        environment = os.environ.copy()
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            part
            for part in (str(REPOSITORY_ROOT), existing_pythonpath)
            if part
        )
        return environment

    def _run_helper(self, lock_path, *arguments):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.helpers.try_global_lock",
                str(lock_path),
                *arguments,
            ],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            check=False,
            capture_output=True,
            text=True,
        )

    def test_excludes_a_second_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "chromium.lock"
            with file_lock(lock_path, wait_seconds=0):
                completed = self._run_helper(lock_path)

        self.assertEqual(
            completed.returncode,
            75,
            completed.stdout + completed.stderr,
        )

    def test_descriptor_lock_is_released_after_abrupt_process_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "chromium.lock"
            crashed = self._run_helper(lock_path, "crash")
            acquired = self._run_helper(lock_path)

        self.assertEqual(
            crashed.returncode,
            0,
            crashed.stdout + crashed.stderr,
        )
        self.assertEqual(
            acquired.returncode,
            0,
            acquired.stdout + acquired.stderr,
        )

    def test_lock_file_is_not_deleted_or_rewritten_as_stale_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "chromium.lock"
            lock_path.write_bytes(b"persistent-lock-inode")
            with file_lock(lock_path, wait_seconds=0):
                pass

            self.assertEqual(
                lock_path.read_bytes(),
                b"persistent-lock-inode",
            )

    def test_wait_uses_a_bounded_polling_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "chromium.lock"
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tests.helpers.try_global_lock",
                    str(lock_path),
                    "hold",
                    "0.4",
                ],
                cwd=REPOSITORY_ROOT,
                env=self._environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(holder.stdout.readline().strip(), "locked")
                started = time.monotonic()
                with self.assertRaises(LockUnavailable):
                    with file_lock(
                        lock_path,
                        wait_seconds=0.1,
                        poll_seconds=0.02,
                    ):
                        pass
                elapsed = time.monotonic() - started
            finally:
                holder.wait(timeout=2)
                holder.stdout.close()
                holder.stderr.close()

        self.assertGreaterEqual(elapsed, 0.08)
        self.assertLess(elapsed, 0.8)
