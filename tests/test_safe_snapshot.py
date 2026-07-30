import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flows.common import safe_snapshot


class SafeSnapshotTests(unittest.TestCase):
    def test_snapshot_reads_one_handle_and_detects_entry_swap(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "published"
            package = parent / "run"
            runtime = root / "runtime"
            package.mkdir(parents=True)
            runtime.mkdir()
            source = package / "payload.bin"
            source.write_bytes(b"canonical")
            snapshot = safe_snapshot.open_directory_snapshot(
                parent,
                "run",
                ("payload.bin",),
            )
            private_path = None
            try:
                self.assertEqual(
                    safe_snapshot.read_file(
                        snapshot,
                        "payload.bin",
                        max_bytes=32,
                    ),
                    b"canonical",
                )
                with safe_snapshot.private_file(
                        snapshot,
                        "payload.bin",
                        runtime_dir=runtime,
                        max_bytes=32,
                        prefix=".snapshot-",
                        suffix=".bin",
                    ) as private:
                    private_path = private.path
                    self.assertFalse(private.stream.closed)
                    metadata = source.stat()
                    os.utime(
                        source,
                        ns=(
                            metadata.st_atime_ns,
                            metadata.st_mtime_ns
                            + 1_000_000_000,
                        ),
                    )

                    private.stream.seek(0)
                    self.assertEqual(
                        private.stream.read(),
                        b"canonical",
                    )
                    self.assertEqual(
                        private.size,
                        len(b"canonical"),
                    )
                    self.assertEqual(len(private.sha256), 64)
                    self.assertFalse(
                        safe_snapshot.is_current(snapshot)
                    )
                self.assertTrue(private.stream.closed)
                self.assertFalse(private_path.exists())
            finally:
                safe_snapshot.close(snapshot)

    def test_posix_open_requests_nofollow_for_relative_entry(self):
        metadata = os.stat_result(
            (stat.S_IFREG | 0o600, 7, 9, 1, 0, 0, 4, 0, 0, 0)
        )
        with patch.object(
            safe_snapshot.os,
            "O_NOFOLLOW",
            0x20000,
            create=True,
        ), patch.object(
            safe_snapshot.os,
            "open",
            return_value=41,
        ) as opened, patch.object(
            safe_snapshot.os,
            "fstat",
            return_value=metadata,
        ), patch.object(
            safe_snapshot.os,
            "stat",
            return_value=metadata,
        ):
            descriptor, _identity = safe_snapshot._open_posix_entry(
                "payload.bin",
                directory_fd=17,
                directory=False,
            )

        self.assertEqual(descriptor, 41)
        self.assertEqual(opened.call_args.kwargs["dir_fd"], 17)
        self.assertEqual(
            opened.call_args.args[1] & 0x20000,
            0x20000,
        )

    def test_missing_posix_directory_closes_parent_descriptor(self):
        with patch.object(
            safe_snapshot.os,
            "open",
            side_effect=(71, FileNotFoundError()),
        ), patch.object(
            safe_snapshot.os,
            "close",
        ) as close:
            result = safe_snapshot._open_posix(
                Path("published"),
                "missing",
                ("payload.bin",),
            )

        self.assertIsNone(result)
        close.assert_called_once_with(71)


if __name__ == "__main__":
    unittest.main()
