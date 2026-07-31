"""Orquestracao atomica da coleta Financeiro Hoje."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

from flows.routerbox_coordination import AlreadyLocked, wait_for_site_lock

from .browser import CollectionError, download_report
from .config import Instance, Settings
from .publication import apply_retention, build_manifest, publish
from .schedule import next_run_at
from .workbook import consolidate, validate


DONE_FILE = "done.json"
HISTORY_FILE = "history.jsonl"
_SAFE_CODE = re.compile(r"\A[A-Z][A-Z0-9_]{0,63}\Z")


class AlreadyRunning(RuntimeError):
    """Outra execucao Financeiro Hoje ainda detem o lock exclusivo."""


class DeadlineExceeded(RuntimeError):
    """O prazo absoluto de uma execucao terminou."""


@contextmanager
def exclusive_run_lock(path: str | Path) -> Iterator[None]:
    """Adquire um lock exclusivo sem apagar um lock de outro processo."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise AlreadyRunning("FINANCEIRO_HOJE_BUSY") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"owner": "financeiro_hoje", "token": token}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = None
        if payload and payload.get("token") == token:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


@contextmanager
def default_context(_instance: Instance) -> Iterator[Any]:
    """Abre e fecha Playwright, browser e contexto em uma unidade de coleta."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            context = browser.new_context(
                accept_downloads=True,
                viewport={"width": 1366, "height": 768},
            )
            try:
                yield context.new_page()
            finally:
                context.close()
        finally:
            browser.close()


def build_run_id(scheduled_for: datetime) -> str:
    """Cria identificador canonicamente aceito pela camada de publicacao."""
    return f"{scheduled_for.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(8)}"


def run_once(
    *,
    settings: Settings | None = None,
    scheduled_for: datetime | None = None,
    downloader: Callable[..., Path] = download_report,
    now: Callable[[], datetime] = datetime.now,
    context_factory: Callable[[Instance], Any] | None = None,
) -> dict[str, Any]:
    """Coleta LOGA e ACERTA e publica somente quando ambas forem validas."""
    settings = settings or Settings.from_mapping(os.environ)
    zone = ZoneInfo(settings.timezone)
    current_time = _in_zone(now(), zone)
    scheduled = (
        _scheduled_in_zone(scheduled_for, zone)
        if scheduled_for is not None
        else current_time
    )
    started_at = current_time
    deadline = scheduled + timedelta(seconds=settings.deadline_seconds)
    run_id = build_run_id(scheduled)
    previous_before_lock = read_done(settings.root)

    try:
        with exclusive_run_lock(settings.root / "financeiro_hoje.lock"):
            previous = read_done(settings.root) or previous_before_lock
            stage = "download"
            company = None
            try:
                _check_deadline(deadline, now, zone)
                run_dir = settings.root / "runs" / run_id
                run_dir.mkdir(parents=True, exist_ok=False)
                period_start = scheduled.date()
                period_end = period_start + timedelta(days=settings.period_days)
                factory = context_factory or (
                    default_context if downloader is download_report else _no_context
                )
                with wait_for_site_lock(
                    settings.root.parent / "routerbox-site.lock",
                    owner="financeiro_hoje",
                    deadline=deadline,
                    now=lambda: _in_zone(now(), zone),
                ):
                    company = "LOGA"
                    loga = collect_one(
                        settings.instances[0], run_dir, period_start, period_end,
                        deadline, downloader, factory, now, zone,
                    )
                    company = "ACERTA"
                    acerta = collect_one(
                        settings.instances[1], run_dir, period_start, period_end,
                        deadline, downloader, factory, now, zone,
                    )
                _check_deadline(deadline, now, zone)
                stage, company = "validation", "LOGA"
                loga_artifact = validate(loga, created_after=scheduled)
                _check_deadline(deadline, now, zone)
                stage, company = "validation", "ACERTA"
                acerta_artifact = validate(acerta, created_after=scheduled)
                _check_deadline(deadline, now, zone)
                stage, company = "consolidation", None
                consolidated = consolidate(
                    loga_artifact.path,
                    acerta_artifact.path,
                    run_dir / "consolidado.xlsx",
                )
                _check_deadline(deadline, now, zone)
                stage = "publication"
                finished_at = _in_zone(now(), zone)
                manifest = build_manifest(
                    loga_artifact,
                    acerta_artifact,
                    consolidated,
                    run_id=run_id,
                    scheduled_for=scheduled,
                    started_at=started_at,
                    finished_at=finished_at,
                    period_start=period_start,
                    period_end=period_end,
                )
                publish(
                    settings.root,
                    run_id,
                    run_dir,
                    manifest,
                    before_promote=lambda: _check_deadline(deadline, now, zone),
                )
                finished_at = _in_zone(now(), zone)
            except Exception as exc:
                if stage == "publication" and _publication_was_promoted(
                    settings.root, run_id
                ):
                    finished_at = _in_zone(now(), zone)
                    payload = _success_payload(
                        run_id=run_id,
                        scheduled_for=scheduled,
                        started_at=started_at,
                        finished_at=finished_at,
                        previous=previous,
                        publication_state="PUBLICATION_DURABILITY_UNCERTAIN",
                    )
                    return _finalize_state(
                        settings.root,
                        payload,
                        now=_in_zone(now(), zone),
                    )
                payload = _failure_payload(
                    previous=previous,
                    run_id=run_id,
                    scheduled_for=scheduled,
                    started_at=started_at,
                    finished_at=_in_zone(now(), zone),
                    exc=exc,
                    stage_hint=stage,
                    company_hint=company,
                )
                return _finalize_state(
                    settings.root,
                    payload,
                    now=_in_zone(now(), zone),
                )

            payload = _success_payload(
                run_id=run_id,
                scheduled_for=scheduled,
                started_at=started_at,
                finished_at=finished_at,
                previous=previous,
            )
            return _finalize_state(
                settings.root,
                payload,
                now=_in_zone(now(), zone),
            )
    except AlreadyRunning:
        return _lock_payload(
            run_id,
            scheduled,
            started_at,
            _in_zone(now(), zone),
            previous_before_lock,
        )


def collect_one(
    instance: Instance,
    run_dir: Path,
    period_start,
    period_end,
    deadline: datetime,
    downloader: Callable[..., Path],
    context_factory: Callable[[Instance], Any],
    now: Callable[[], datetime],
    zone: ZoneInfo,
) -> Path:
    """Executa uma coleta dentro de um contexto que sempre e fechado."""
    _check_deadline(deadline, now, zone)
    destination = run_dir / f"original_{instance.name.casefold()}.xlsx"
    evidence_root = run_dir.parent.parent / "evidence" / run_dir.name / instance.name.casefold()
    browser_deadline = deadline.astimezone().replace(tzinfo=None)
    with context_factory(instance) as page:
        try:
            path = downloader(
                page,
                instance,
                destination,
                period_start,
                period_end,
                browser_deadline,
                evidence_root=evidence_root,
            )
        except CollectionError as exc:
            exc.company = instance.name
            raise
    _check_deadline(deadline, now, zone)
    return Path(path)


def read_done(root: str | Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((Path(root) / DONE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _publication_was_promoted(root: str | Path, run_id: str) -> bool:
    """Confirma a promocao pelo ponteiro e pelo pacote sem expor erros brutos."""
    root_path = Path(root)
    try:
        pointer = json.loads((root_path / "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    expected_manifest = f"published/{run_id}/manifest.json"
    if not isinstance(pointer, dict) or pointer.get("run_id") != run_id:
        return False
    if pointer.get("manifest") != expected_manifest:
        return False
    package = root_path / "published" / run_id
    manifest_path = package / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return (
        package.is_dir()
        and isinstance(manifest, dict)
        and manifest.get("run_id") == run_id
        and manifest.get("status") == "success"
    )


def write_done_atomic(root: str | Path, payload: dict[str, Any]) -> None:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    target = root_path / DONE_FILE
    temporary = _temporary_path(target)
    _write_json_replace(temporary, target, payload)


def append_history(root: str | Path, payload: dict[str, Any]) -> None:
    logs = Path(root) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    target = logs / HISTORY_FILE
    temporary = _temporary_path(target)
    try:
        existing = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    _write_text_replace(temporary, target, existing + line + "\n")


def _finalize_state(
    root: str | Path,
    payload: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Persiste estado e faz housekeeping sem invalidar publicacao concluida."""
    state_ok = True
    for operation in (write_done_atomic, append_history):
        try:
            operation(root, payload)
        except Exception:
            state_ok = False
    try:
        apply_retention(root, now=now)
    except Exception:
        housekeeping = "HOUSEKEEPING_FAILED"
    else:
        housekeeping = "COMPLETED"
    result = dict(payload)
    result["state_persisted"] = state_ok
    result["state_error"] = None if state_ok else "STATE_PERSIST_FAILED"
    result["housekeeping"] = housekeeping
    return result


def _write_json_replace(temporary: Path, target: Path, payload: dict[str, Any]) -> None:
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _write_text_replace(temporary: Path, target: Path, content: str) -> None:
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _temporary_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.{secrets.token_hex(12)}.tmp")


@contextmanager
def _no_context(_instance: Instance) -> Iterator[None]:
    yield None


def _in_zone(value: datetime, zone: ZoneInfo) -> datetime:
    return _host_aware(value).astimezone(zone)


def _host_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.astimezone()
    return value


def _scheduled_in_zone(value: datetime, zone: ZoneInfo) -> datetime:
    """Interpreta ``scheduled_for`` sem offset como wall time do fluxo."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _check_deadline(
    deadline: datetime,
    now: Callable[[], datetime],
    zone: ZoneInfo,
) -> None:
    if _in_zone(now(), zone) >= deadline:
        raise DeadlineExceeded("deadline")


def _success_payload(
    *,
    run_id: str,
    scheduled_for: datetime,
    started_at: datetime,
    finished_at: datetime,
    previous: dict[str, Any] | None,
    publication_state: str | None = None,
) -> dict[str, Any]:
    payload = {
        "success": True,
        "run_id": run_id,
        "scheduled_for": scheduled_for.isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "company": None,
        "stage": "published",
        "code": None,
        "message": "LOGA e ACERTA publicadas.",
        "last_success": finished_at.isoformat(),
        "next_scheduled_for": next_run_at(finished_at).isoformat(),
        "alert_active": False,
        "recovered_at": (
            started_at.isoformat()
            if previous and previous.get("alert_active")
            else None
        ),
    }
    if publication_state is not None:
        payload["publication_state"] = publication_state
    return payload


def _failure_payload(
    *, previous, run_id, scheduled_for, started_at, finished_at, exc,
    stage_hint: str, company_hint: str | None,
):
    if isinstance(exc, DeadlineExceeded):
        company, stage, code = None, "deadline", "DEADLINE_EXCEEDED"
    elif isinstance(exc, CollectionError):
        company, stage, code = _company_from_exception(exc), "download", _safe_code(exc.code)
    elif isinstance(exc, AlreadyLocked):
        company, stage, code = None, "lock", "ROUTERBOX_SITE_BUSY"
    else:
        company, stage, code = company_hint, stage_hint, "RUN_FAILED"
    return {
        "success": False,
        "run_id": run_id,
        "scheduled_for": scheduled_for.isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "company": company,
        "stage": stage,
        "code": code,
        "message": _failure_message(company, stage),
        "last_success": previous.get("last_success") if previous else None,
        "next_scheduled_for": next_run_at(finished_at).isoformat(),
        "alert_active": True,
        "recovered_at": None,
    }


def _lock_payload(run_id, scheduled_for, started_at, finished_at, previous):
    return {
        "success": False,
        "run_id": run_id,
        "scheduled_for": scheduled_for.isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "company": None,
        "stage": "lock",
        "code": "FINANCEIRO_HOJE_BUSY",
        "message": "Execucao Financeiro Hoje ja esta em andamento.",
        "last_success": previous.get("last_success") if previous else None,
        "next_scheduled_for": next_run_at(finished_at).isoformat(),
        "alert_active": previous.get("alert_active", True) if previous else True,
        "recovered_at": None,
        "state_skipped": "ACTIVE_RUN",
    }


def _company_from_exception(exc: CollectionError) -> str | None:
    company = getattr(exc, "company", None)
    return company if company in {"LOGA", "ACERTA"} else None


def _safe_code(code: object) -> str:
    value = str(code)
    return value if _SAFE_CODE.fullmatch(value) else "RUN_FAILED"


def _failure_message(company: str | None, stage: str) -> str:
    if stage == "deadline":
        return "Prazo da execucao Financeiro Hoje excedido."
    if stage == "lock":
        return "RouterBox ocupado para a execucao Financeiro Hoje."
    if company:
        return f"Falha na coleta de {company}."
    return "Falha no processamento Financeiro Hoje."
