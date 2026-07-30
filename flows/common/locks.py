from contextlib import contextmanager
import errno
import os
from pathlib import Path
import time


if os.name == "nt":
    import msvcrt
else:
    import fcntl


class LockUnavailable(RuntimeError):
    """Raised when a descriptor lock cannot be acquired before its deadline."""


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _is_contention(error: OSError) -> bool:
    if os.name == "nt":
        return error.errno in {
            errno.EACCES,
            errno.EAGAIN,
            errno.EDEADLK,
            errno.EPERM,
        }
    return error.errno in {errno.EACCES, errno.EAGAIN}


@contextmanager
def descriptor_lock(
    descriptor: int,
    *,
    wait_seconds: float,
    poll_seconds: float = 1.0,
):
    """Hold a crash-safe cross-process lock on an open descriptor."""
    wait_seconds = float(wait_seconds)
    poll_seconds = float(poll_seconds)
    if wait_seconds < 0 or poll_seconds <= 0:
        raise ValueError("Invalid lock timing.")
    acquired = False
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            try:
                _lock_descriptor(descriptor)
                acquired = True
                break
            except OSError as error:
                if not _is_contention(error):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockUnavailable("LOCKED") from None
                time.sleep(min(poll_seconds, remaining))
        yield
    finally:
        if acquired:
            _unlock_descriptor(descriptor)


@contextmanager
def file_lock(
    path: Path,
    *,
    wait_seconds: float,
    poll_seconds: float = 1.0,
):
    """Hold a crash-safe cross-process lock on ``path``'s descriptor."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR,
        0o600,
    )
    try:
        with descriptor_lock(
            descriptor,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
        ):
            yield
    finally:
        os.close(descriptor)
