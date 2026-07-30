from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes"}


def _secret(
    values: Mapping[str, str],
    file_key: str,
    value_key: str,
    fallback_key: str,
) -> str:
    file_path = str(values.get(file_key, "")).strip()
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").rstrip("\r\n")

    value = str(values.get(value_key, "")).strip()
    if value:
        return value
    return str(values.get(fallback_key, "")).strip()


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

        raw_root = str(
            values.get(
                "FINANCEIRO_MEDICAO_RUNTIME_ROOT",
                "/app/data_pipeline/financeiro_medicao",
            )
        ).strip()
        runtime_root = Path(raw_root)
        if (
            not (runtime_root.is_absolute() or raw_root.startswith("/"))
            or runtime_root.name != "financeiro_medicao"
        ):
            raise ValueError("FINANCEIRO_MEDICAO_RUNTIME_ROOT inválido")

        schedule_hour = int(values.get("FINANCEIRO_MEDICAO_SCHEDULE_HOUR", "0"))
        schedule_minute = int(values.get("FINANCEIRO_MEDICAO_SCHEDULE_MINUTE", "1"))
        lock_wait_seconds = int(
            values.get("FINANCEIRO_MEDICAO_LOCK_WAIT_SECONDS", "1200")
        )
        if not 0 <= schedule_hour <= 23:
            raise ValueError("FINANCEIRO_MEDICAO_SCHEDULE_HOUR inválido")
        if not 0 <= schedule_minute <= 59:
            raise ValueError("FINANCEIRO_MEDICAO_SCHEDULE_MINUTE inválido")
        if not 0 <= lock_wait_seconds <= 3600:
            raise ValueError("FINANCEIRO_MEDICAO_LOCK_WAIT_SECONDS inválido")

        return cls(
            loga_url=loga_url,
            runtime_root=runtime_root,
            schedule_enabled=_as_bool(
                values.get("FINANCEIRO_MEDICAO_SCHEDULE_ENABLED", "false")
            ),
            schedule_hour=schedule_hour,
            schedule_minute=schedule_minute,
            timezone=str(
                values.get("FINANCEIRO_MEDICAO_TIMEZONE", "America/Sao_Paulo")
            ),
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
            ),
        )

    @property
    def storage_state_path(self) -> Path:
        return self.runtime_root / "runtime" / "loga-storage-state.json"
