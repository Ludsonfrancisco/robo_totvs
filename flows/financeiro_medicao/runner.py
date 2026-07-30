from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from .browser import authenticated_page
from .bundle import BundleDurabilityError, build_bundle
from .config import Settings
from .cycles import window_for
from .loga import CollectionError, collect
from .workbook import WorkbookInvalid
from flows.common.locks import LockUnavailable, file_lock


RETRYABLE_ERRORS = {
    "NAVIGATION_TIMEOUT",
    "DOWNLOAD_TIMEOUT",
    "DOWNLOAD_FAILED",
}
_KNOWN_COLLECTION_ERRORS = RETRYABLE_ERRORS | {
    "AUTH_EXPIRED",
    "AUTH_STATE_FAILED",
    "AUTH_STATE_CLEANUP_FAILED",
    "DOWNLOAD_TEMP_CLEANUP_FAILED",
    "FILTER_MISMATCH",
    "UNSUPPORTED_PLATFORM",
}
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PRIVATE_PREFIX = ".financeiro-medicao-"
_PRIVATE_SUFFIX = ".xlsx"
_CLEANUP_DELAYS = (0, 0.01, 0.05)


def _ensure_directories(runtime_root: Path) -> None:
    runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("inbox", "quarantine", "published", "runtime"):
        (runtime_root / name).mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )


def _private_workbook_path(runtime_dir: Path) -> Path:
    return runtime_dir / (
        f"{_PRIVATE_PREFIX}{uuid4().hex}{_PRIVATE_SUFFIX}"
    )


def _remove_private_file(path: Path, runtime_dir: Path) -> None:
    path = Path(path)
    runtime_dir = Path(runtime_dir)
    try:
        is_owned = (
            path.parent.resolve() == runtime_dir.resolve()
            and path.name.startswith(_PRIVATE_PREFIX)
            and path.name.endswith(_PRIVATE_SUFFIX)
        )
    except OSError:
        is_owned = False
    if not is_owned:
        raise OSError("Invalid private download path.")

    last_error = None
    for delay in _CLEANUP_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            path.unlink(missing_ok=True)
            return
        except OSError as error:
            last_error = error
    raise OSError("Private download cleanup failed.") from last_error


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, LockUnavailable):
        return "LOCKED"
    if isinstance(error, WorkbookInvalid):
        return "WORKBOOK_INVALID"
    if isinstance(error, BundleDurabilityError):
        return "BUNDLE_DURABILITY_FAILED"
    if isinstance(error, CollectionError):
        if error.code in _KNOWN_COLLECTION_ERRORS:
            return error.code
        return "COLLECTION_FAILED"
    return "UNEXPECTED_ERROR"


def _published_run_id(error: Exception) -> str | None:
    current = error
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, BundleDurabilityError):
            candidate = current.published.name
            if _RUN_ID.fullmatch(candidate):
                return candidate
        current = current.__cause__
    return None


def _next_scheduled_for(settings, moment: datetime) -> str | None:
    if getattr(settings, "schedule_enabled", False) is not True:
        return None
    timezone = ZoneInfo(settings.timezone)
    local_moment = moment.astimezone(timezone)
    candidate = local_moment.replace(
        hour=settings.schedule_hour,
        minute=settings.schedule_minute,
        second=0,
        microsecond=0,
    )
    if candidate <= local_moment:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def _lock_wait_seconds(settings) -> float:
    value = getattr(settings, "lock_wait_seconds", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return value


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_done(runtime_root: Path, payload: dict) -> None:
    target = runtime_root / "done.json"
    temporary = runtime_root / f".done.json.{uuid4().hex}.tmp"
    descriptor = None
    created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_directory(runtime_root)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            temporary.unlink(missing_ok=True)


def _collect_bundle(
    *,
    page,
    settings,
    window,
    collector,
    bundle_builder,
    started_at,
    scheduled_for,
    image_revision,
    clock,
):
    runtime_dir = Path(settings.runtime_root) / "runtime"
    private_file = _private_workbook_path(runtime_dir)
    primary_error = None
    cleanup_error = None
    published = None
    try:
        try:
            collected = Path(
                collector(page, window, settings, private_file)
            )
            if collected.resolve() != private_file.resolve():
                raise CollectionError("DOWNLOAD_FAILED")
            finished_at = clock()
            published = bundle_builder(
                runtime_root=Path(settings.runtime_root),
                source=private_file,
                window=window,
                scheduled_for=scheduled_for,
                started_at=started_at,
                finished_at=finished_at,
                image_revision=image_revision,
            )
        except BaseException as error:
            primary_error = error
    finally:
        try:
            _remove_private_file(private_file, runtime_dir)
        except OSError as error:
            cleanup_error = error

    if primary_error is not None and not isinstance(
        primary_error,
        Exception,
    ):
        raise primary_error
    if cleanup_error is not None:
        cleanup_failure = CollectionError(
            "DOWNLOAD_TEMP_CLEANUP_FAILED"
        )
        if primary_error is not None:
            raise cleanup_failure from primary_error
        raise cleanup_failure from cleanup_error
    if primary_error is not None:
        raise primary_error
    return Path(published)


def _status_payload(
    *,
    settings,
    window,
    started_at,
    finished_at,
    run_id,
    error_code,
):
    return {
        "success": not error_code,
        "error_code": error_code,
        "run_id": run_id,
        "cycle_id": window.cycle_id,
        "mode": window.mode,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "next_scheduled_for": _next_scheduled_for(
            settings,
            finished_at,
        ),
    }


def run_once(
    *,
    settings: Settings | None = None,
    day: date | None = None,
    page_factory=None,
    collector=None,
    bundle_builder=None,
    sleeper=None,
    clock=None,
    scheduled_for: datetime | None = None,
    image_revision: str | None = None,
):
    settings = settings or Settings.from_mapping(os.environ)
    runtime_root = Path(settings.runtime_root)
    _ensure_directories(runtime_root)
    page_factory = page_factory or authenticated_page
    collector = collector or collect
    bundle_builder = bundle_builder or build_bundle
    sleeper = sleeper or time.sleep
    if clock is None:
        timezone = ZoneInfo(settings.timezone)
        clock = lambda: datetime.now(timezone)

    started_at = clock()
    scheduled_for = scheduled_for or started_at
    day = day or started_at.date()
    window = window_for(day)
    image_revision = (
        str(image_revision).strip()
        if image_revision is not None
        else os.environ.get("IMAGE_REVISION", "").strip()
    ) or "unknown"
    flow_lock = runtime_root / "runtime" / "financeiro_medicao.lock"
    chromium_lock = runtime_root.parent / "runtime" / "chromium.lock"
    lock_wait_seconds = _lock_wait_seconds(settings)
    run_id = None
    error_code = ""

    try:
        with file_lock(flow_lock, wait_seconds=lock_wait_seconds):
            try:
                with file_lock(
                    chromium_lock,
                    wait_seconds=lock_wait_seconds,
                ):
                    with page_factory(settings) as page:
                        for attempt in range(1, 4):
                            try:
                                published = _collect_bundle(
                                    page=page,
                                    settings=settings,
                                    window=window,
                                    collector=collector,
                                    bundle_builder=bundle_builder,
                                    started_at=started_at,
                                    scheduled_for=scheduled_for,
                                    image_revision=image_revision,
                                    clock=clock,
                                )
                                candidate_run_id = published.name
                                if not _RUN_ID.fullmatch(candidate_run_id):
                                    raise RuntimeError(
                                        "Invalid bundle identifier."
                                    )
                                run_id = candidate_run_id
                                error_code = ""
                                break
                            except Exception as error:
                                published_run_id = _published_run_id(
                                    error
                                )
                                if published_run_id is not None:
                                    run_id = published_run_id
                                error_code = _safe_error_code(error)
                                if (
                                    error_code not in RETRYABLE_ERRORS
                                    or attempt == 3
                                ):
                                    break
                                sleeper(1)
            except Exception as error:
                error_code = _safe_error_code(error)

            finished_at = clock()
            payload = _status_payload(
                settings=settings,
                window=window,
                started_at=started_at,
                finished_at=finished_at,
                run_id=run_id,
                error_code=error_code,
            )
            _write_done(runtime_root, payload)
            return payload
    except LockUnavailable:
        error_code = "LOCKED"

    finished_at = clock()
    payload = _status_payload(
        settings=settings,
        window=window,
        started_at=started_at,
        finished_at=finished_at,
        run_id=run_id,
        error_code=error_code,
    )
    return payload


if __name__ == "__main__":
    raise SystemExit(0 if run_once()["success"] else 1)
