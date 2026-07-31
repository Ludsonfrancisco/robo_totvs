"""Publicacao atomica e retencao dos artefatos do Financeiro Hoje."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import errno
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any, Callable, Mapping


AUTOMATION_VERSION = "1"
SCHEMA_VERSION = 1
RUN_RETENTION = timedelta(days=7)
PUBLISHED_RETENTION = timedelta(days=7)
EVIDENCE_RETENTION = timedelta(days=14)
HISTORY_RETENTION = timedelta(days=30)
MINIMUM_PUBLISHED = 3
TEMPORARY_PACKAGE_RETENTION = timedelta(days=1)
_RUN_ID_PATTERN = re.compile(r"\A\d{8}T\d{6}-[A-Za-z0-9]+\Z")
_UNSUPPORTED_DIRECTORY_SYNC_ERRORS = frozenset({
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
})


class StorageUnsafeError(ValueError):
    """Um diretório gerenciado aponta para fora do storage esperado."""


@dataclass(frozen=True)
class StoragePathState:
    exists: bool
    is_dir: bool
    is_file: bool
    redirect: bool


def build_manifest(
    loga: Any,
    acerta: Any,
    consolidated: Any,
    *,
    run_id: str | None = None,
    scheduled_for: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    automation_version: str = AUTOMATION_VERSION,
) -> dict[str, Any]:
    """Monta o manifesto publico sem incluir dados da planilha ou credenciais."""
    finished = finished_at or datetime.now().astimezone()
    started = started_at or finished
    scheduled = scheduled_for or started
    resolved_run_id = run_id or _artifact_path(consolidated).parent.name
    _validate_run_id(resolved_run_id)
    start = period_start or scheduled.date()
    end = period_end or start + timedelta(days=10)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": resolved_run_id,
        "scheduled_for": _iso_with_offset(scheduled),
        "started_at": _iso_with_offset(started),
        "finished_at": _iso_with_offset(finished),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "automation_version": automation_version,
        "status": "success",
        "sources": {
            "LOGA": _artifact_manifest("LOGA", loga),
            "ACERTA": _artifact_manifest("ACERTA", acerta),
        },
        "consolidated": _artifact_manifest("consolidated", consolidated),
    }


def publish(
    root: str | Path,
    run_id: str,
    run_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    before_promote: Callable[[], None] | None = None,
) -> Path:
    """Publica um pacote fechado e so entao aponta ``current.json`` para ele."""
    _validate_run_id(run_id)
    root_path = Path(root)
    _ensure_managed_directory(root_path)
    if _is_storage_redirect(root_path / "runs"):
        raise StorageUnsafeError("storage gerenciado inseguro")
    source_dir = Path(run_dir)
    source_workbook = source_dir / "consolidado.xlsx"
    if not source_workbook.is_file():
        raise FileNotFoundError(source_workbook)

    published_root = root_path / "published"
    _ensure_managed_directory(published_root)
    package = published_root / run_id
    temporary_package = published_root / f"{run_id}.tmp"
    if _storage_path_state(package).exists:
        raise FileExistsError(f"pacote de publicacao ja existe: {run_id}")
    _remove_stale_temporary_package(temporary_package)

    temporary_package.mkdir()
    try:
        _copy_synced(source_workbook, temporary_package / "consolidado.xlsx")
        _write_json_synced(temporary_package / "manifest.json", dict(manifest))
        _sync_directory(temporary_package)
        os.replace(temporary_package, package)
        _sync_directory(published_root)
    except Exception:
        temporary_state = _storage_path_state(temporary_package)
        if temporary_state.exists and temporary_state.is_dir and not temporary_state.redirect:
            shutil.rmtree(temporary_package)
        raise

    if before_promote is not None:
        before_promote()
    _promote_current(root_path, run_id)
    return package


def apply_retention(root: str | Path, *, now: datetime) -> None:
    """Remove artefatos expirados, sem jamais apagar o pacote corrente."""
    root_path = Path(root)
    if _is_storage_redirect(root_path):
        return
    current_run_id = _current_run_id(root_path)
    reference = _as_utc(now)

    _remove_expired_directories(
        root_path / "runs",
        reference - RUN_RETENTION,
        protected={current_run_id} if current_run_id else set(),
    )
    _retain_published(root_path / "published", reference, current_run_id)
    _remove_expired_directories(
        root_path / "evidence",
        reference - EVIDENCE_RETENTION,
        protected={current_run_id} if current_run_id else set(),
    )
    logs_root = root_path / "logs"
    if not _is_storage_redirect(logs_root):
        _retain_history(logs_root / "history.jsonl", reference - HISTORY_RETENTION)


def _artifact_manifest(name: str, artifact: Any) -> dict[str, Any]:
    path = _artifact_path(artifact)
    return {
        "name": name,
        "path": path.name,
        "rows": int(getattr(artifact, "rows")),
        "size": int(getattr(artifact, "size")),
        "sha256": str(getattr(artifact, "sha256")),
    }


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id invalido")


def _ensure_managed_directory(path: Path) -> None:
    if _is_storage_redirect(path):
        raise StorageUnsafeError("storage gerenciado inseguro")
    path.mkdir(parents=True, exist_ok=True)
    if _is_storage_redirect(path):
        raise StorageUnsafeError("storage gerenciado inseguro")


def _is_storage_redirect(path: Path) -> bool:
    """Detecta symlink e reparse point sem resolver ou seguir o destino."""
    return _storage_path_state(path).redirect


def _storage_path_state(path: Path) -> StoragePathState:
    """Inspeciona um path exclusivamente por ``lstat``, sem seguir seu alvo."""
    try:
        details = path.lstat()
    except FileNotFoundError:
        return StoragePathState(False, False, False, False)
    except OSError:
        return StoragePathState(False, False, False, False)
    mode = getattr(details, "st_mode", 0)
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    redirect = stat.S_ISLNK(mode) or bool(attributes & reparse_point)
    return StoragePathState(
        exists=True,
        is_dir=stat.S_ISDIR(mode),
        is_file=stat.S_ISREG(mode),
        redirect=redirect,
    )


def _artifact_path(artifact: Any) -> Path:
    return Path(getattr(artifact, "path", artifact))


def _iso_with_offset(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("horario do manifesto deve conter offset")
    return value.isoformat()


def _copy_synced(source: Path, target: Path) -> None:
    with source.open("rb") as input_handle, target.open("xb") as output_handle:
        shutil.copyfileobj(input_handle, output_handle)
        output_handle.flush()
        os.fsync(output_handle.fileno())


def _write_json_synced(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _promote_current(root: Path, run_id: str) -> None:
    pointer = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "manifest": f"published/{run_id}/manifest.json",
    }
    temporary = root / "current.json.tmp"
    root.mkdir(parents=True, exist_ok=True)
    temporary_state = _storage_path_state(temporary)
    if temporary_state.redirect:
        raise StorageUnsafeError("storage gerenciado inseguro")
    if temporary_state.exists:
        temporary.unlink()
    try:
        _write_json_synced(temporary, pointer)
        os.replace(temporary, root / "current.json")
        _sync_directory(root)
    finally:
        temporary_state = _storage_path_state(temporary)
        if temporary_state.exists and not temporary_state.redirect:
            temporary.unlink()


def _remove_stale_temporary_package(temporary: Path) -> None:
    """Recupera somente tmp antigo sob o lock exclusivo da Task 6.

    Um tmp recente pode pertencer a uma tentativa ainda viva, portanto bloqueia
    a publicacao. A limpeza nunca segue symlinks e ocorre apenas quando nao ha
    pacote final com o mesmo run_id.
    """
    state = _storage_path_state(temporary)
    if not state.exists:
        return
    cutoff = datetime.now(timezone.utc) - TEMPORARY_PACKAGE_RETENTION
    if state.redirect or not state.is_dir or _modified_at(temporary) >= cutoff:
        raise FileExistsError(f"pacote de publicacao ja existe: {temporary.stem}")
    shutil.rmtree(temporary)


def _sync_directory(path: Path) -> None:
    """Sincroniza metadata de diretorio em POSIX; Windows nao oferece a operacao."""
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        if exc.errno in _UNSUPPORTED_DIRECTORY_SYNC_ERRORS:
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in _UNSUPPORTED_DIRECTORY_SYNC_ERRORS:
            raise
    finally:
        os.close(descriptor)


def _current_run_id(root: Path) -> str | None:
    pointer_path = root / "current.json"
    if _is_storage_redirect(pointer_path):
        return None
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    run_id = pointer.get("run_id") if isinstance(pointer, dict) else None
    return run_id if isinstance(run_id, str) else None


def _retain_published(root: Path, now: datetime, current_run_id: str | None) -> None:
    root_state = _storage_path_state(root)
    if root_state.redirect or not root_state.is_dir:
        return
    packages = []
    for path in root.iterdir():
        state = _storage_path_state(path)
        if state.is_dir and not state.redirect and not path.name.endswith(".tmp"):
            packages.append(path)
    newest = {
        package.name
        for package in sorted(packages, key=_modified_at, reverse=True)[:MINIMUM_PUBLISHED]
    }
    protected = newest | ({current_run_id} if current_run_id else set())
    _remove_expired_directories(root, now - PUBLISHED_RETENTION, protected=protected)


def _remove_expired_directories(
    root: Path,
    cutoff: datetime,
    *,
    protected: set[str] | None = None,
) -> None:
    root_state = _storage_path_state(root)
    if root_state.redirect or not root_state.is_dir:
        return
    protected = protected or set()
    for candidate in root.iterdir():
        state = _storage_path_state(candidate)
        if (
            state.is_dir
            and not state.redirect
            and candidate.name not in protected
            and _modified_at(candidate) < cutoff
        ):
            shutil.rmtree(candidate)


def _retain_history(path: Path, cutoff: datetime) -> None:
    state = _storage_path_state(path)
    if state.redirect or not state.is_file:
        return
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        timestamp = _history_timestamp(line)
        if timestamp is None or timestamp >= cutoff:
            kept.append(line)
    _write_text_synced(path, "\n".join(kept) + ("\n" if kept else ""))


def _history_timestamp(line: str) -> datetime | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("finished_at", "started_at", "scheduled_for"):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            continue
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return _as_utc(parsed)
    return None


def _write_text_synced(path: Path, content: str) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    if _storage_path_state(temporary).redirect:
        return
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _modified_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
