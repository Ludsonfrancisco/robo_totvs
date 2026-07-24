from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    loga_url: str
    runtime_root: Path
    schedule_enabled: bool
    schedule_hour: int
    schedule_minute: int
    timezone: str

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "Settings":
        loga_url = str(values.get("MULTIPLICA_LOGA_URL", "")).strip()
        if urlparse(loga_url).scheme != "https":
            raise ValueError("MULTIPLICA_LOGA_URL deve usar HTTPS")

        raw_root = str(
            values.get(
                "MULTIPLICA_RUNTIME_ROOT",
                "/app/data_pipeline/multiplica",
            )
        )
        runtime_root = Path(raw_root)
        if (
            not (runtime_root.is_absolute() or raw_root.startswith("/"))
            or runtime_root.name != "multiplica"
            or "routerbox_backlog" in raw_root.casefold()
        ):
            raise ValueError("MULTIPLICA_RUNTIME_ROOT inválido")

        settings = cls(
            loga_url=loga_url,
            runtime_root=runtime_root,
            schedule_enabled=_as_bool(
                values.get("MULTIPLICA_SCHEDULE_ENABLED", "false")
            ),
            schedule_hour=int(values.get("MULTIPLICA_SCHEDULE_HOUR", "23")),
            schedule_minute=int(
                values.get("MULTIPLICA_SCHEDULE_MINUTE", "50")
            ),
            timezone=str(
                values.get("MULTIPLICA_TIMEZONE", "America/Sao_Paulo")
            ),
        )
        for name in ("auth", "inbox", "processed", "runtime"):
            (settings.runtime_root / name).mkdir(parents=True, exist_ok=True)
        return settings

    @property
    def storage_state_path(self) -> Path:
        return self.runtime_root / "auth" / "loga-storage-state.json"
