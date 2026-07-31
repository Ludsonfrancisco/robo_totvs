import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


class AlreadyLocked(RuntimeError):
    pass


class SiteLock:
    def __init__(self, path: Path, *, owner: str):
        self.path = Path(path)
        self.owner = owner
        self.token = f"{owner}:{os.getpid()}"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise AlreadyLocked("ROUTERBOX_SITE_BUSY") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"owner": self.owner, "token": self.token}, handle)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if payload.get("token") == self.token:
            self.path.unlink(missing_ok=True)


def finance_may_start(now, next_backlog_at, guard_seconds=90):
    return (next_backlog_at - now).total_seconds() > guard_seconds


@contextmanager
def wait_for_site_lock(
    path,
    *,
    owner,
    deadline,
    now=datetime.now,
    sleep=time.sleep,
    poll_seconds=1,
):
    while now() < deadline:
        lease = SiteLock(path, owner=owner)
        try:
            lease.__enter__()
        except AlreadyLocked:
            sleep(min(poll_seconds, max(0, (deadline - now()).total_seconds())))
            continue
        try:
            yield lease
        finally:
            lease.__exit__(None, None, None)
        return
    raise AlreadyLocked("ROUTERBOX_SITE_BUSY deadline")
