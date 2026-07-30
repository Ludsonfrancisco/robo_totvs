from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _as_bool(value: str, default: bool = False, *, name: str = "booleano") -> bool:
    if value is None:
        return default
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} inválido")


def _secret(
    values: Mapping[str, str],
    file_key: str,
    value_key: str,
    fallback_key: str,
    *,
    normalize_values: bool = True,
) -> str:
    file_path = str(values.get(file_key, "")).strip()
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").rstrip("\r\n")

    value = str(values.get(value_key, ""))
    fallback = str(values.get(fallback_key, ""))
    if normalize_values:
        value = value.strip()
        fallback = fallback.strip()
    if value:
        return value
    return fallback


def _runtime_root(values: Mapping[str, str]) -> Path:
    raw_root = str(
        values.get(
            "FINANCEIRO_MEDICAO_RUNTIME_ROOT",
            "/app/data_pipeline/financeiro_medicao",
        )
    ).strip()
    runtime_root = Path(raw_root)
    # Accept the Linux container path even when configuration tests run on Windows.
    if (
        not (runtime_root.is_absolute() or raw_root.startswith("/"))
        or runtime_root.name != "financeiro_medicao"
    ):
        raise ValueError("FINANCEIRO_MEDICAO_RUNTIME_ROOT inválido")
    return runtime_root


def _bounded_int(
    values: Mapping[str, str],
    name: str,
    default: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(values.get(name, default))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} inválido") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} inválido")
    return value


def _timezone(values: Mapping[str, str]) -> str:
    timezone = str(
        values.get("FINANCEIRO_MEDICAO_TIMEZONE", "America/Sao_Paulo")
    ).strip()
    if not timezone:
        raise ValueError("FINANCEIRO_MEDICAO_TIMEZONE inválido")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError("FINANCEIRO_MEDICAO_TIMEZONE inválido") from error
    return timezone


@dataclass(frozen=True)
class Settings:
    loga_url: str
    runtime_root: Path
    schedule_enabled: bool
    schedule_hour: int
    schedule_minute: int
    timezone: str
    lock_wait_seconds: int
    username: str
    password: str

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "Settings":
        loga_url = str(values.get("FINANCEIRO_MEDICAO_LOGA_URL", "")).strip()
        parsed_url = urlparse(loga_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ValueError("FINANCEIRO_MEDICAO_LOGA_URL deve usar HTTPS")

        runtime_root = _runtime_root(values)
        schedule_hour = _bounded_int(
            values, "FINANCEIRO_MEDICAO_SCHEDULE_HOUR", "0", 0, 23
        )
        schedule_minute = _bounded_int(
            values, "FINANCEIRO_MEDICAO_SCHEDULE_MINUTE", "1", 0, 59
        )
        lock_wait_seconds = _bounded_int(
            values, "FINANCEIRO_MEDICAO_LOCK_WAIT_SECONDS", "1200", 0, 3600
        )

        return cls(
            loga_url=loga_url,
            runtime_root=runtime_root,
            schedule_enabled=_as_bool(
                values.get("FINANCEIRO_MEDICAO_SCHEDULE_ENABLED", "false"),
                name="FINANCEIRO_MEDICAO_SCHEDULE_ENABLED",
            ),
            schedule_hour=schedule_hour,
            schedule_minute=schedule_minute,
            timezone=_timezone(values),
            lock_wait_seconds=lock_wait_seconds,
            username=_secret(
                values,
                "LOGA_DASHBOARD_USER_FILE",
                "LOGA_DASHBOARD_USER",
                "MULTIPLICA_LOGA_USER",
            ),
            password=_secret(
                values,
                "LOGA_DASHBOARD_PASSWORD_FILE",
                "LOGA_DASHBOARD_PASSWORD",
                "MULTIPLICA_LOGA_PASSWORD",
                normalize_values=False,
            ),
        )

    @property
    def storage_state_path(self) -> Path:
        return self.runtime_root / "runtime" / "loga-storage-state.json"
