from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from loguru import logger

from flows.common.locks import LockUnavailable, file_lock

from . import runner


RETRY_BASE_SECONDS = os.environ.get(
    "FINANCEIRO_MEDICAO_RETRY_BASE_SECONDS",
    "60",
)
RETRY_MAX_SECONDS = os.environ.get(
    "FINANCEIRO_MEDICAO_RETRY_MAX_SECONDS",
    "900",
)
TRANSIENT_ERRORS = set(runner.RETRYABLE_ERRORS) | {
    "LOCKED",
    "UNEXPECTED_ERROR",
}
KNOWN_ERRORS = TRANSIENT_ERRORS | {
    "AUTH_EXPIRED",
    "AUTH_STATE_FAILED",
    "AUTH_STATE_CLEANUP_FAILED",
    "BUNDLE_DURABILITY_FAILED",
    "COLLECTION_FAILED",
    "CONFIG_INVALID",
    "DOWNLOAD_TEMP_CLEANUP_FAILED",
    "FILTER_MISMATCH",
    "UNSUPPORTED_PLATFORM",
    "WORKBOOK_INVALID",
}


def _runtime_paths(settings) -> tuple[Path, Path, Path]:
    runtime = Path(settings.runtime_root) / "runtime"
    return (
        runtime / "schedule.signal.json",
        runtime / "schedule-watermark.json",
        runtime / ".schedule.signal.json.claim.lock",
    )


def _local(moment: datetime, timezone: ZoneInfo) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone)
    return moment.astimezone(timezone)


def _parse_datetime(value, timezone: ZoneInfo) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _local(
            datetime.fromisoformat(value.replace("Z", "+00:00")),
            timezone,
        )
    except ValueError:
        return None


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
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
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _day_completed(settings, local_date) -> bool:
    _, watermark_path, _ = _runtime_paths(settings)
    watermark = _read_json(watermark_path)
    if (
        watermark.get("local_date") == local_date.isoformat()
        and watermark.get("outcome") in {"success", "terminal"}
    ):
        return True

    done = _read_json(Path(settings.runtime_root) / "done.json")
    if done.get("success") is not True:
        return False
    finished = _parse_datetime(
        done.get("finished_at"),
        ZoneInfo(settings.timezone),
    )
    return finished is not None and finished.date() == local_date


def next_event_at(now: datetime, settings) -> datetime:
    timezone = ZoneInfo(settings.timezone)
    local_now = now if now.tzinfo is None else now.astimezone(timezone)
    scheduled_today = local_now.replace(
        hour=settings.schedule_hour,
        minute=settings.schedule_minute,
        second=0,
        microsecond=0,
    )
    signal_path, _, _ = _runtime_paths(settings)
    retry_at = _parse_datetime(
        _read_json(signal_path).get("next_attempt_at"),
        timezone,
    )
    if retry_at is not None:
        return retry_at.replace(tzinfo=None) if now.tzinfo is None else retry_at
    if _day_completed(settings, local_now.date()):
        return scheduled_today + timedelta(days=1)
    return scheduled_today


def _claims(signal_path: Path) -> list[Path]:
    if not signal_path.parent.exists():
        return []
    prefix = f".{signal_path.name}.claimed."
    owned = []
    for candidate in signal_path.parent.iterdir():
        suffix = candidate.name.removeprefix(prefix)
        if (
            candidate.is_file()
            and candidate.name.startswith(prefix)
            and len(suffix) == 32
            and all(character in "0123456789abcdef" for character in suffix)
        ):
            owned.append(candidate)
    return sorted(owned, key=lambda path: path.name)


def _reconcile_claims(
    signal_path: Path,
    settings,
    now: datetime,
) -> bool:
    timezone = ZoneInfo(settings.timezone)
    for claim in _claims(signal_path):
        claimed_at = _parse_datetime(
            _read_json(claim).get("claimed_at"),
            timezone,
        )
        if (
            claimed_at is not None
            and now - claimed_at < timedelta(seconds=10)
        ):
            return False
        flow_lock = (
            Path(settings.runtime_root)
            / "runtime"
            / "financeiro_medicao.lock"
        )
        try:
            with file_lock(flow_lock, wait_seconds=0):
                pass
        except LockUnavailable:
            return False
        if signal_path.exists():
            claim.unlink(missing_ok=True)
        else:
            os.replace(claim, signal_path)
        _fsync_directory(signal_path.parent)
    return True


def request_run(
    settings,
    scheduled_for: datetime,
    *,
    now: datetime,
) -> None:
    signal_path, _, claim_lock = _runtime_paths(settings)
    timezone = ZoneInfo(settings.timezone)
    scheduled_for = _local(scheduled_for, timezone)
    now = _local(now, timezone)
    if _day_completed(settings, scheduled_for.date()):
        return
    with file_lock(claim_lock, wait_seconds=0):
        if not _reconcile_claims(signal_path, settings, now):
            return
        if not signal_path.exists():
            _atomic_write(
                signal_path,
                {
                    "schema_version": 1,
                    "scheduled_for": scheduled_for.isoformat(),
                    "attempt": 0,
                    "next_attempt_at": now.isoformat(),
                },
            )


def _claim_if_due(
    settings,
    now: datetime,
) -> tuple[Path, dict] | None:
    signal_path, _, claim_lock = _runtime_paths(settings)
    timezone = ZoneInfo(settings.timezone)
    now = _local(now, timezone)
    with file_lock(claim_lock, wait_seconds=0):
        if not _reconcile_claims(signal_path, settings, now):
            return None
        if not signal_path.exists():
            return None
        payload = _read_json(signal_path)
        next_attempt = _parse_datetime(
            payload.get("next_attempt_at"),
            timezone,
        )
        scheduled_for = _parse_datetime(
            payload.get("scheduled_for"),
            timezone,
        )
        if next_attempt is None or scheduled_for is None:
            signal_path.unlink(missing_ok=True)
            _fsync_directory(signal_path.parent)
            logger.error(
                "[financeiro_medicao] Signal inválido descartado; "
                "error_code=INVALID_SCHEDULE_SIGNAL."
            )
            return None
        if next_attempt > now:
            return None
        claim = signal_path.with_name(
            f".{signal_path.name}.claimed.{uuid4().hex}"
        )
        os.replace(signal_path, claim)
        _fsync_directory(signal_path.parent)
        payload["claimed_at"] = now.isoformat()
        _atomic_write(claim, payload)
        return claim, payload


def _finish_claim(claim: Path) -> None:
    claim.unlink(missing_ok=True)
    _fsync_directory(claim.parent)


def _write_watermark(
    settings,
    scheduled_for: datetime,
    outcome: str,
    error_code: str = "",
) -> None:
    _, watermark_path, _ = _runtime_paths(settings)
    payload = {
        "schema_version": 1,
        "local_date": scheduled_for.date().isoformat(),
        "scheduled_for": scheduled_for.isoformat(),
        "outcome": outcome,
    }
    if error_code:
        payload["error_code"] = error_code
    _atomic_write(watermark_path, payload)


def _retry_delay(attempt: int) -> int:
    try:
        base = int(RETRY_BASE_SECONDS)
        maximum = int(RETRY_MAX_SECONDS)
        if not 1 <= base <= maximum <= 86400:
            raise ValueError
    except (TypeError, ValueError):
        base, maximum = 60, 900
    return min(base * (2 ** (attempt - 1)), maximum)


def _restore_retry(
    settings,
    claim: Path,
    payload: dict,
    now: datetime,
) -> None:
    signal_path, _, claim_lock = _runtime_paths(settings)
    attempt = int(payload.get("attempt", 0)) + 1
    retry = {
        "schema_version": 1,
        "scheduled_for": payload["scheduled_for"],
        "attempt": attempt,
        "next_attempt_at": (
            now + timedelta(seconds=_retry_delay(attempt))
        ).isoformat(),
    }
    with file_lock(claim_lock, wait_seconds=0):
        if not signal_path.exists():
            _atomic_write(signal_path, retry)
        _finish_claim(claim)


def _error_code(payload: dict) -> str:
    value = payload.get("error_code")
    return value if value in KNOWN_ERRORS else "UNEXPECTED_ERROR"


def run_signal_if_due(
    settings,
    *,
    now: datetime,
) -> bool | None:
    claimed = _claim_if_due(settings, now)
    if claimed is None:
        return None
    claim, signal = claimed
    scheduled_for = _parse_datetime(
        signal.get("scheduled_for"),
        ZoneInfo(settings.timezone),
    )
    if scheduled_for is None:
        _finish_claim(claim)
        return None
    if _day_completed(settings, scheduled_for.date()):
        _finish_claim(claim)
        return True

    try:
        result = runner.run_once(
            day=scheduled_for.date(),
            scheduled_for=scheduled_for,
        )
    except ValueError:
        result = {"success": False, "error_code": "CONFIG_INVALID"}
    except Exception:
        result = {"success": False, "error_code": "UNEXPECTED_ERROR"}

    if result.get("success") is True:
        _write_watermark(settings, scheduled_for, "success")
        _finish_claim(claim)
        logger.info("[financeiro_medicao] Coleta agendada concluída.")
        return True

    error_code = _error_code(result)
    attempt = int(signal.get("attempt", 0)) + 1
    if (
        error_code in TRANSIENT_ERRORS
        and not (error_code == "UNEXPECTED_ERROR" and attempt >= 3)
    ):
        _restore_retry(settings, claim, signal, now)
        logger.warning(
            "[financeiro_medicao] Retry agendado; "
            f"error_code={error_code}."
        )
        return False

    _write_watermark(settings, scheduled_for, "terminal", error_code)
    _finish_claim(claim)
    logger.warning(
        "[financeiro_medicao] Tentativa encerrada; "
        f"error_code={error_code}."
    )
    return False
