from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import stat
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from .bundle import (
    BundleCollisionError,
    PublishedBundleDetails,
    inspect_committed_publication,
    inspect_published_bundle,
    validate_manifest_snapshot,
)
from .cycles import window_for
from flows.common.locks import descriptor_lock, file_lock
from flows.common.safe_snapshot import (
    close as close_snapshot,
    directory_snapshot,
    is_current as snapshot_is_current,
    open_directory_snapshot,
    read_file,
)


PAYLOAD_KEYS = {
    "success",
    "error_code",
    "run_id",
    "cycle_id",
    "mode",
    "started_at",
    "finished_at",
    "next_scheduled_for",
}
EVENT_ERROR_CODES = {
    "AUTH_EXPIRED",
    "AUTH_STATE_CLEANUP_FAILED",
    "AUTH_STATE_FAILED",
    "BUNDLE_COLLISION",
    "BUNDLE_DURABILITY_FAILED",
    "COLLECTION_FAILED",
    "DOWNLOAD_FAILED",
    "DOWNLOAD_TEMP_CLEANUP_FAILED",
    "DOWNLOAD_TIMEOUT",
    "FILTER_MISMATCH",
    "LOCKED",
    "NAVIGATION_TIMEOUT",
    "UNEXPECTED_ERROR",
    "UNSUPPORTED_PLATFORM",
    "WORKBOOK_INVALID",
}
PARTIAL_PUBLICATION_ERRORS = {
    "BUNDLE_DURABILITY_FAILED",
    "DOWNLOAD_TEMP_CLEANUP_FAILED",
}
RECEIPT_KEYS = {
    "schema_version",
    "event_id",
    "scheduled_for",
    "run_id",
    "manifest",
    "workbook_size",
    "workbook_sha256",
}
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def scheduled_event_identity(scheduled_for: datetime) -> tuple[str, str]:
    if (
        not isinstance(scheduled_for, datetime)
        or scheduled_for.tzinfo is None
        or scheduled_for.utcoffset() is None
    ):
        raise ValueError("Scheduled event must include timezone.")
    timezone_name = getattr(
        scheduled_for.tzinfo,
        "key",
        str(scheduled_for.tzinfo),
    )
    canonical = (
        "financeiro_medicao|"
        f"{timezone_name}|{scheduled_for.isoformat()}"
    )
    event_id = uuid5(
        NAMESPACE_URL,
        f"event|{canonical}",
    ).hex
    run_id = uuid5(
        NAMESPACE_URL,
        f"bundle|{event_id}",
    ).hex
    return event_id, run_id


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _is_reparse(metadata) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def require_real_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ValueError("Invalid private directory.") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or _is_reparse(metadata)
    ):
        raise ValueError("Invalid private directory.")


def mkdir_durable(path: Path) -> None:
    path = Path(path)
    require_real_directory(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        require_real_directory(path)
        os.chmod(path, 0o700)
        return
    require_real_directory(path)
    os.chmod(path, 0o700)
    fsync_directory(path)
    fsync_directory(path.parent)


def _entry_identity(metadata) -> tuple[int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
    )


def _require_regular_lock(metadata) -> None:
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or _is_reparse(metadata)
        or getattr(metadata, "st_nlink", 1) != 1
    ):
        raise ValueError("Invalid event owner lock.")


@contextmanager
def _posix_event_owner_lock(
    runtime_root: Path,
    filename: str,
    *,
    wait_seconds: float,
):
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors = []
    try:
        root_descriptor = os.open(runtime_root, directory_flags)
        descriptors.append(root_descriptor)
        runtime_descriptor = os.open(
            "runtime",
            directory_flags,
            dir_fd=root_descriptor,
        )
        descriptors.append(runtime_descriptor)
        events_descriptor = os.open(
            "events",
            directory_flags,
            dir_fd=runtime_descriptor,
        )
        descriptors.append(events_descriptor)
        lock_descriptor = os.open(
            filename,
            os.O_CREAT
            | os.O_RDWR
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=events_descriptor,
        )
        descriptors.append(lock_descriptor)
        lock_metadata = os.stat(
            filename,
            dir_fd=events_descriptor,
            follow_symlinks=False,
        )
        _require_regular_lock(lock_metadata)
        if _entry_identity(os.fstat(lock_descriptor)) != (
            _entry_identity(lock_metadata)
        ):
            raise ValueError("Invalid event owner lock.")
        with descriptor_lock(
            lock_descriptor,
            wait_seconds=wait_seconds,
        ):
            if (
                _entry_identity(os.fstat(root_descriptor))
                != _entry_identity(runtime_root.lstat())
                or _entry_identity(os.fstat(runtime_descriptor))
                != _entry_identity(
                    os.stat(
                        "runtime",
                        dir_fd=root_descriptor,
                        follow_symlinks=False,
                    )
                )
                or _entry_identity(os.fstat(events_descriptor))
                != _entry_identity(
                    os.stat(
                        "events",
                        dir_fd=runtime_descriptor,
                        follow_symlinks=False,
                    )
                )
                or _entry_identity(os.fstat(lock_descriptor))
                != _entry_identity(
                    os.stat(
                        filename,
                        dir_fd=events_descriptor,
                        follow_symlinks=False,
                    )
                )
            ):
                raise ValueError("Event owner lock changed.")
            yield
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@contextmanager
def _windows_event_owner_lock(
    runtime_root: Path,
    filename: str,
    *,
    wait_seconds: float,
):
    runtime_dir = runtime_root / "runtime"
    events_dir = runtime_dir / "events"
    before = tuple(
        _entry_identity(path.lstat())
        for path in (runtime_root, runtime_dir, events_dir)
    )
    for path in (runtime_root, runtime_dir, events_dir):
        require_real_directory(path)
    lock_path = events_dir / filename
    descriptor = os.open(
        lock_path,
        os.O_CREAT
        | os.O_RDWR
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0),
        0o600,
    )
    try:
        lock_metadata = lock_path.lstat()
        _require_regular_lock(lock_metadata)
        if _entry_identity(os.fstat(descriptor)) != _entry_identity(
            lock_metadata
        ):
            raise ValueError("Invalid event owner lock.")
        with descriptor_lock(
            descriptor,
            wait_seconds=wait_seconds,
        ):
            after = tuple(
                _entry_identity(path.lstat())
                for path in (runtime_root, runtime_dir, events_dir)
            )
            for path in (runtime_root, runtime_dir, events_dir):
                require_real_directory(path)
            if (
                before != after
                or _entry_identity(os.fstat(descriptor))
                != _entry_identity(lock_path.lstat())
            ):
                raise ValueError("Event owner lock changed.")
            yield
    finally:
        os.close(descriptor)


@contextmanager
def event_owner_lock(
    runtime_root: Path,
    event_id: str,
    *,
    wait_seconds: float,
):
    if not re.fullmatch(r"[0-9a-f]{32}", event_id):
        raise ValueError("Invalid event identity.")
    runtime_root = Path(runtime_root)
    runtime_dir = runtime_root / "runtime"
    require_real_directory(runtime_root)
    require_real_directory(runtime_dir)
    events_dir = runtime_dir / "events"
    mkdir_durable(events_dir)
    filename = f"{event_id}.lock"
    owner = (
        _posix_event_owner_lock
        if os.name == "posix"
        else _windows_event_owner_lock
    )
    with owner(
        runtime_root,
        filename,
        wait_seconds=wait_seconds,
    ):
        yield


def atomic_json_write(target: Path, payload: dict) -> None:
    mkdir_durable(target.parent)
    temporary = target.with_name(
        f".{target.name}.{uuid4().hex}.tmp"
    )
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as stream:
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
        os.chmod(target, 0o600)
        fsync_directory(target.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def event_result_path(runtime_root: Path, event_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", event_id):
        raise ValueError("Invalid event identity.")
    return (
        Path(runtime_root)
        / "runtime"
        / "events"
        / f"{event_id}.result.json"
    )


def event_receipt_path(runtime_root: Path, event_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", event_id):
        raise ValueError("Invalid event identity.")
    return (
        Path(runtime_root)
        / "runtime"
        / "events"
        / f"{event_id}.receipt.json"
    )


def _read_private_event_json(
    runtime_root: Path,
    filename: str,
) -> dict | None:
    with directory_snapshot(
        Path(runtime_root) / "runtime",
        "events",
        (filename,),
        missing_files_none=True,
    ) as opened:
        if opened is None:
            return None
        try:
            value = json.loads(
                read_file(
                    opened,
                    filename,
                    max_bytes=1024 * 1024,
                ).decode("utf-8")
            )
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError("Invalid private event file.") from error
        if not snapshot_is_current(opened):
            raise ValueError("Invalid private event file.")
        if not isinstance(value, dict):
            raise ValueError("Invalid private event file.")
        return value


def write_event_result(
    runtime_root: Path,
    event_id: str,
    scheduled_for: datetime,
    payload: dict,
) -> None:
    atomic_json_write(
        event_result_path(runtime_root, event_id),
        {
            "schema_version": 1,
            "event_id": event_id,
            "scheduled_for": scheduled_for.isoformat(),
            "payload": payload,
        },
    )


def write_success_receipt(
    runtime_root: Path,
    event_id: str,
    scheduled_for: datetime,
    details: PublishedBundleDetails,
) -> None:
    atomic_json_write(
        event_receipt_path(runtime_root, event_id),
        {
            "schema_version": 1,
            "event_id": event_id,
            "scheduled_for": scheduled_for.isoformat(),
            "run_id": details.manifest["run_id"],
            "manifest": details.manifest,
            "workbook_size": details.workbook_size,
            "workbook_sha256": details.workbook_sha256,
        },
    )


def read_success_receipt(
    runtime_root: Path,
    event_id: str,
    scheduled_for: datetime,
    *,
    expected_result: dict | None,
) -> PublishedBundleDetails | None:
    receipt = _read_private_event_json(
        runtime_root,
        event_receipt_path(runtime_root, event_id).name,
    )
    if receipt is None:
        return None
    try:
        expected_event_id, run_id = scheduled_event_identity(
            scheduled_for
        )
        window = window_for(scheduled_for.date())
        if (
            set(receipt) != RECEIPT_KEYS
            or receipt["schema_version"] != 1
            or receipt["event_id"] != expected_event_id
            or event_id != expected_event_id
            or receipt["scheduled_for"]
            != scheduled_for.isoformat()
            or receipt["run_id"] != run_id
            or type(receipt["workbook_size"]) is not int
            or receipt["workbook_size"] <= 0
            or not isinstance(receipt["workbook_sha256"], str)
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                receipt["workbook_sha256"],
            )
        ):
            raise ValueError
        manifest = validate_manifest_snapshot(
            receipt["manifest"],
            run_id=run_id,
            window=window,
            scheduled_for=scheduled_for,
            expected_result=expected_result,
        )
        if (
            receipt["workbook_size"]
            != manifest["workbook_size"]
            or receipt["workbook_sha256"]
            != manifest["workbook_sha256"]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Invalid event success receipt.") from error
    try:
        proof = inspect_committed_publication(
            runtime_root=Path(runtime_root),
            run_id=run_id,
            window=window,
            scheduled_for=scheduled_for,
            expected_result=expected_result,
        )
    except BundleCollisionError as error:
        raise ValueError("Invalid event success receipt.") from error
    if (
        proof is None
        or proof.manifest != manifest
        or proof.workbook_size != receipt["workbook_size"]
        or proof.workbook_sha256 != receipt["workbook_sha256"]
    ):
        raise ValueError("Invalid event success receipt.")
    return proof


def quarantine_event_result(
    runtime_root: Path,
    event_id: str,
) -> Path | None:
    journal = event_result_path(runtime_root, event_id)
    quarantined = journal.with_name(
        f".{journal.name}.corrupt.{uuid4().hex}"
    )
    opened = open_directory_snapshot(
        Path(runtime_root) / "runtime",
        "events",
        (journal.name,),
        missing_files_none=True,
    )
    if opened is None:
        return None
    try:
        if not snapshot_is_current(opened):
            raise ValueError("Invalid event result journal.")
        if opened["posix"]:
            os.replace(
                journal.name,
                quarantined.name,
                src_dir_fd=opened["directory_fd"],
                dst_dir_fd=opened["directory_fd"],
            )
            os.fsync(opened["directory_fd"])
        else:
            close_snapshot(opened)
            opened = None
            require_real_directory(journal.parent)
            metadata = journal.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or _is_reparse(metadata)
            ):
                raise ValueError("Invalid event result journal.")
            os.replace(journal, quarantined)
            fsync_directory(journal.parent)
    finally:
        close_snapshot(opened)
    return quarantined


def read_event_result(
    runtime_root: Path,
    event_id: str,
    scheduled_for: datetime,
) -> dict | None:
    path = event_result_path(runtime_root, event_id)
    try:
        journal = _read_private_event_json(
            runtime_root,
            path.name,
        )
    except ValueError as error:
        raise ValueError("Invalid event result journal.") from error
    if journal is None:
        return None
    payload = journal.get("payload") if isinstance(journal, dict) else None
    if (
        not isinstance(journal, dict)
        or set(journal)
        != {
            "schema_version",
            "event_id",
            "scheduled_for",
            "payload",
        }
        or journal.get("schema_version") != 1
        or journal.get("event_id") != event_id
        or journal.get("scheduled_for") != scheduled_for.isoformat()
        or not isinstance(payload, dict)
        or set(payload) != PAYLOAD_KEYS
        or not isinstance(payload.get("success"), bool)
        or not isinstance(payload.get("error_code"), str)
    ):
        raise ValueError("Invalid event result journal.")

    try:
        expected_event_id, expected_run_id = scheduled_event_identity(
            scheduled_for
        )
        window = window_for(scheduled_for.date())
        started_at = datetime.fromisoformat(payload["started_at"])
        finished_at = datetime.fromisoformat(payload["finished_at"])
        next_value = payload["next_scheduled_for"]
        next_scheduled_for = (
            None
            if next_value is None
            else datetime.fromisoformat(next_value)
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Invalid event result journal.") from error

    success = payload["success"]
    error_code = payload["error_code"]
    run_id = payload["run_id"]
    timestamps = (started_at, finished_at)
    if (
        event_id != expected_event_id
        or type(payload["cycle_id"]) is not str
        or payload["cycle_id"] != window.cycle_id
        or type(payload["mode"]) is not str
        or payload["mode"] != window.mode
        or any(
            value.tzinfo is None or value.utcoffset() is None
            for value in timestamps
        )
        or not scheduled_for <= started_at <= finished_at
        or (
            next_scheduled_for is not None
            and (
                next_scheduled_for.tzinfo is None
                or next_scheduled_for.utcoffset() is None
                or next_scheduled_for <= finished_at
            )
        )
        or (success and error_code != "")
        or (
            not success
            and (
                not error_code
                or error_code not in EVENT_ERROR_CODES
            )
        )
        or (run_id is not None and type(run_id) is not str)
    ):
        raise ValueError("Invalid event result journal.")

    if run_id is not None:
        if (
            run_id != expected_run_id
            or (
                not success
                and error_code not in PARTIAL_PUBLICATION_ERRORS
            )
        ):
            raise ValueError("Invalid event result journal.")
        try:
            inbox_details = inspect_published_bundle(
                runtime_root=Path(runtime_root),
                run_id=run_id,
                window=window,
                scheduled_for=scheduled_for,
                expected_result=payload if success else None,
            )
        except BundleCollisionError as error:
            raise ValueError("Invalid event result journal.") from error
        details = inbox_details
        if details is None:
            try:
                details = inspect_committed_publication(
                    runtime_root=Path(runtime_root),
                    run_id=run_id,
                    window=window,
                    scheduled_for=scheduled_for,
                    expected_result=payload if success else None,
                )
            except BundleCollisionError as error:
                raise ValueError(
                    "Invalid event result journal."
                ) from error
        if success and inbox_details is None:
            try:
                receipt_details = read_success_receipt(
                    runtime_root,
                    event_id,
                    scheduled_for,
                    expected_result=payload,
                )
            except ValueError as error:
                raise ValueError(
                    "Invalid event result journal."
                ) from error
            if (
                receipt_details is None
                or details is None
                or receipt_details.manifest != details.manifest
                or receipt_details.workbook_size
                != details.workbook_size
                or receipt_details.workbook_sha256
                != details.workbook_sha256
            ):
                raise ValueError("Invalid event result journal.")
        if details is None:
            raise ValueError("Invalid event result journal.")
    elif success:
        raise ValueError("Invalid event result journal.")
    return payload


def next_scheduled_for(settings, moment: datetime) -> str | None:
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


def payload_from_published(
    settings,
    details: PublishedBundleDetails,
) -> dict:
    manifest = details.manifest
    finished_at = datetime.fromisoformat(manifest["finished_at"])
    return {
        "success": True,
        "error_code": "",
        "run_id": manifest["run_id"],
        "cycle_id": manifest["cycle_id"],
        "mode": manifest["mode"],
        "started_at": manifest["started_at"],
        "finished_at": manifest["finished_at"],
        "next_scheduled_for": next_scheduled_for(
            settings,
            finished_at,
        ),
    }


def _status_payload(
    *,
    settings,
    window,
    started_at,
    finished_at,
    run_id,
    error_code,
) -> dict:
    return {
        "success": not error_code,
        "error_code": error_code,
        "run_id": run_id,
        "cycle_id": window.cycle_id,
        "mode": window.mode,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "next_scheduled_for": next_scheduled_for(
            settings,
            finished_at,
        ),
    }


def reconcile_published_event_locked(
    settings,
    event_id: str,
    scheduled_for: datetime,
) -> dict | None:
    expected_event_id, run_id = scheduled_event_identity(
        scheduled_for
    )
    if event_id != expected_event_id:
        raise ValueError("Event identity does not match schedule.")
    window = window_for(scheduled_for.date())
    try:
        details = inspect_published_bundle(
            runtime_root=Path(settings.runtime_root),
            run_id=run_id,
            window=window,
            scheduled_for=scheduled_for,
        )
    except BundleCollisionError:
        moment = datetime.now(ZoneInfo(settings.timezone))
        payload = _status_payload(
            settings=settings,
            window=window,
            started_at=moment,
            finished_at=moment,
            run_id=None,
            error_code="BUNDLE_COLLISION",
        )
        write_event_result(
            Path(settings.runtime_root),
            event_id,
            scheduled_for,
            payload,
        )
        atomic_json_write(
            Path(settings.runtime_root) / "done.json",
            payload,
        )
        return payload
    if details is None:
        try:
            details = inspect_committed_publication(
                runtime_root=Path(settings.runtime_root),
                run_id=run_id,
                window=window,
                scheduled_for=scheduled_for,
            )
        except BundleCollisionError:
            moment = datetime.now(ZoneInfo(settings.timezone))
            payload = _status_payload(
                settings=settings,
                window=window,
                started_at=moment,
                finished_at=moment,
                run_id=None,
                error_code="BUNDLE_COLLISION",
            )
            write_event_result(
                Path(settings.runtime_root),
                event_id,
                scheduled_for,
                payload,
            )
            atomic_json_write(
                Path(settings.runtime_root) / "done.json",
                payload,
            )
            return payload
    if details is None:
        try:
            details = read_success_receipt(
                Path(settings.runtime_root),
                event_id,
                scheduled_for,
                expected_result=None,
            )
        except ValueError:
            details = None
        if details is None:
            return None
    payload = payload_from_published(settings, details)
    write_success_receipt(
        Path(settings.runtime_root),
        event_id,
        scheduled_for,
        details,
    )
    write_event_result(
        Path(settings.runtime_root),
        event_id,
        scheduled_for,
        payload,
    )
    atomic_json_write(
        Path(settings.runtime_root) / "done.json",
        payload,
    )
    return payload


def reconcile_published_event(
    settings,
    event_id: str,
    scheduled_for: datetime,
) -> dict | None:
    runtime_root = Path(settings.runtime_root)
    flow_lock = (
        runtime_root / "runtime" / "financeiro_medicao.lock"
    )
    with file_lock(flow_lock, wait_seconds=0):
        try:
            existing = read_event_result(
                runtime_root,
                event_id,
                scheduled_for,
            )
        except ValueError:
            existing = None
        if existing is not None:
            return existing
        return reconcile_published_event_locked(
            settings,
            event_id,
            scheduled_for,
        )
