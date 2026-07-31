from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlsplit


def _bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().casefold() in {"1", "true", "yes"}


def resolve_root(value: object) -> Path:
    raw_value = str(value)
    root_value = Path(raw_value)
    root = root_value.expanduser().resolve(strict=False)
    if (
        not (
            root_value.is_absolute()
            or raw_value.startswith(("/", "\\"))
        )
        or root.name != "financeiro_hoje"
        or "routerbox_backlog" in root.parts
    ):
        raise ValueError("FINANCEIRO_HOJE_ROOT inválido")
    return root


_HOSTNAME_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.I)


def _validated_https_url(value: object, name: str) -> str:
    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} URL inv\u00e1lida") from exc
    if parsed.scheme.casefold() != "https" or not hostname:
        raise ValueError(f"{name} URL HTTPS obrigat\u00f3ria")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").rstrip(".")
        ipaddress.ip_address(ascii_hostname)
    except ValueError:
        if not ascii_hostname or any(
            not _HOSTNAME_LABEL.fullmatch(label)
            for label in ascii_hostname.split(".")
        ):
            raise ValueError(f"{name} URL com hostname inv\u00e1lido")
    return url


@dataclass(frozen=True)
class Instance:
    name: str
    url: str
    user: str
    password: str


@dataclass(frozen=True)
class Settings:
    root: Path
    schedule_enabled: bool
    timezone: str
    deadline_seconds: int
    period_days: int
    poll_seconds: int
    instances: tuple[Instance, ...]

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "Settings":
        data_pipeline_dir = Path(
            str(values.get("DATA_PIPELINE_DIR", "/app/data_pipeline"))
        ).expanduser().resolve(strict=False)
        root = resolve_root(values.get(
            "FINANCEIRO_HOJE_ROOT",
            data_pipeline_dir / "financeiro_hoje",
        ))
        if root.parent != data_pipeline_dir:
            raise ValueError("FINANCEIRO_HOJE_ROOT deve usar DATA_PIPELINE_DIR")
        loga_url = _validated_https_url(
            values.get("ROUTERBOX_LOGA_URL"), "ROUTERBOX_LOGA_URL"
        )
        acerta_url = _validated_https_url(
            values.get("ROUTERBOX_ACERTA_URL"), "ROUTERBOX_ACERTA_URL"
        )
        user = str(values.get("ROUTERBOX_USER", "")).strip()
        acerta_pass = str(values.get("ROUTERBOX_PASS", "")).strip()
        loga_pass = str(values.get("ROUTERBOX_LOGA_PASS", "")).strip()
        if not all((user, acerta_pass, loga_pass)):
            raise ValueError("Credenciais RouterBox obrigatórias")
        settings = cls(
            root=root,
            schedule_enabled=_bool(values.get("FINANCEIRO_HOJE_SCHEDULE_ENABLED")),
            timezone=str(values.get("FINANCEIRO_HOJE_TIMEZONE", "America/Sao_Paulo")),
            deadline_seconds=int(values.get("FINANCEIRO_HOJE_DEADLINE_SECONDS", 480)),
            period_days=int(values.get("FINANCEIRO_HOJE_PERIOD_DAYS", 10)),
            poll_seconds=int(values.get("FINANCEIRO_HOJE_POLL_SECONDS", 5)),
            instances=(
                Instance("LOGA", loga_url, user, loga_pass),
                Instance("ACERTA", acerta_url, user, acerta_pass),
            ),
        )
        for name in ("runs", "published", "evidence", "logs"):
            (root / name).mkdir(parents=True, exist_ok=True)
        return settings
