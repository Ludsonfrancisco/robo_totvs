"""Worker do robô TOTVS — scheduler diário + signal-driven (retry).

Combina dois disparos:

1. **Scheduler**: dorme até ROBOT_SCHEDULE_HOUR:ROBOT_SCHEDULE_MINUTE
   (default 06:00) e executa `main.main([])` em modo padrão.

2. **Signal-driven**: a cada `WORKER_POLL_INTERVAL` (default 5s), checa
   se existe `run.signal` no volume. Se sim, lê payload, executa imediatamente
   e consome o signal. Usado pelo botão "Reprocessar falhas" do Portal D+.

Signal payload (JSON):
    {"mode": "full"}              ← idêntico ao scheduler
    {"mode": "retry-falhos"}      ← chama main.main(["--retry-falhos"])

Arquivos produzidos no volume `DATA_PIPELINE_DIR`:

    run.log        sink loguru extra (tail visível no Portal D+ durante retry)
    run.done       JSON enriquecido com lista de técnicos OK / falhas
    signal.ready   criado apenas se houve sucessos (parcial OK)

run.done payload:
    {
      "success": bool,                       ← exit_code == 0
      "message": str,
      "started_at": ISO,
      "finished_at": ISO,
      "exit_code": int | None,
      "mode": "scheduled" | "full" | "retry-falhos",
      "tecnicos_total": int,
      "tecnicos_ok": int,
      "tecnicos_falhos": [
        {"code": "HK", "name": "...", "erro_msg": "..."}
      ]
    }

Variáveis de ambiente:
    DATA_PIPELINE_DIR        default: /app/data_pipeline
    ROBOT_SCHEDULE_HOUR      default: 6
    ROBOT_SCHEDULE_MINUTE    default: 0
    ROBOT_RUN_ON_START       default: false
    ROBOT_INCLUDE_DISMISSED  default: false  (true = passa --incluir-desligados em modo scheduled/full)
    ROBOT_AUTO_RETRY         default: true   (true = re-tenta 1x apos falha total com 0 sucessos)
    ROBOT_RETRY_DELAY        default: 300    (segundos de espera antes do retry; default 5 min)
    WORKER_POLL_INTERVAL     default: 5  (segundos do loop signal)
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from loguru import logger

from flows.common.locks import LockUnavailable, file_lock
from flows.financeiro_medicao import schedule as financeiro_schedule
from flows.financeiro_medicao.config import Settings as FinanceiroMedicaoSettings

DATA_PIPELINE_DIR = Path(os.environ.get("DATA_PIPELINE_DIR", "/app/data_pipeline"))
GLOBAL_CHROMIUM_LOCK = DATA_PIPELINE_DIR / "runtime" / "chromium.lock"
CHROMIUM_LOCK_WAIT_SECONDS = 0
SCHEDULE_HOUR = int(os.environ.get("ROBOT_SCHEDULE_HOUR", "6"))
SCHEDULE_MINUTE = int(os.environ.get("ROBOT_SCHEDULE_MINUTE", "0"))
RUN_ON_START = os.environ.get("ROBOT_RUN_ON_START", "false").lower() in ("1", "true", "yes")
INCLUDE_DISMISSED = os.environ.get("ROBOT_INCLUDE_DISMISSED", "false").lower() in ("1", "true", "yes")
AUTO_RETRY = os.environ.get("ROBOT_AUTO_RETRY", "true").lower() in ("1", "true", "yes")
RETRY_DELAY_S = int(os.environ.get("ROBOT_RETRY_DELAY", "300"))
POLL_INTERVAL_S = int(os.environ.get("WORKER_POLL_INTERVAL", "5"))
PROTHEUS_ENABLED = os.environ.get("PROTHEUS_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)
MULTIPLICA_SCHEDULE_ENABLED = os.environ.get(
    "MULTIPLICA_SCHEDULE_ENABLED", "false"
).lower() in ("1", "true", "yes")
MULTIPLICA_SCHEDULE_HOUR = int(
    os.environ.get("MULTIPLICA_SCHEDULE_HOUR", "23")
)
MULTIPLICA_SCHEDULE_MINUTE = int(
    os.environ.get("MULTIPLICA_SCHEDULE_MINUTE", "50")
)
FINANCEIRO_MEDICAO_TIMEZONE = os.environ.get(
    "FINANCEIRO_MEDICAO_TIMEZONE",
    "America/Sao_Paulo",
)
FINANCEIRO_MEDICAO_SCHEDULE_ENABLED = os.environ.get(
    "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED",
    "false",
).lower() in ("1", "true", "yes")
FINANCEIRO_MEDICAO_SCHEDULE_HOUR = os.environ.get(
    "FINANCEIRO_MEDICAO_SCHEDULE_HOUR",
    "0",
)
FINANCEIRO_MEDICAO_SCHEDULE_MINUTE = os.environ.get(
    "FINANCEIRO_MEDICAO_SCHEDULE_MINUTE",
    "1",
)
FINANCEIRO_MEDICAO_RUNTIME_ROOT = os.environ.get(
    "FINANCEIRO_MEDICAO_RUNTIME_ROOT",
    "/app/data_pipeline/financeiro_medicao",
)

# RouterBox Backlog hourly scheduler
ROUTERBOX_ENABLED = os.environ.get("ROUTERBOX_HOURLY_ENABLED", "true").lower() in ("1", "true", "yes")
ROUTERBOX_INTERVAL_MIN = int(os.environ.get("ROUTERBOX_INTERVAL_MINUTES", "30"))
ROUTERBOX_ON_START = os.environ.get("ROUTERBOX_RUN_ON_START", "false").lower() in ("1", "true", "yes")
ROUTERBOX_START_MINUTES = int(os.environ.get("ROUTERBOX_START_HOUR", "5")) * 60 + int(os.environ.get("ROUTERBOX_START_MINUTE", "30"))  # 330 = 5:30
ROUTERBOX_END_MINUTES = int(os.environ.get("ROUTERBOX_END_HOUR", "22")) * 60 + int(os.environ.get("ROUTERBOX_END_MINUTE", "0"))     # 1320 = 22:00

SIGNAL_FILE = DATA_PIPELINE_DIR / "run.signal"
LOG_FILE = DATA_PIPELINE_DIR / "run.log"
DONE_FILE = DATA_PIPELINE_DIR / "run.done"
READY_FILE = DATA_PIPELINE_DIR / "signal.ready"
MULTIPLICA_SIGNAL_FILE = (
    DATA_PIPELINE_DIR / "multiplica" / "multiplica.signal"
)

# RouterBox Backlog artifacts
ROUTERBOX_DIR = Path(os.environ.get("ROUTERBOX_OUTPUT_DIR", "/app/data_pipeline/routerbox_backlog"))
ROUTERBOX_DONE_FILE = ROUTERBOX_DIR / "run_routerbox.done"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _ensure_dirs() -> None:
    DATA_PIPELINE_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_PIPELINE_DIR / "runtime").mkdir(parents=True, exist_ok=True)
    (DATA_PIPELINE_DIR / "entrada").mkdir(parents=True, exist_ok=True)
    (DATA_PIPELINE_DIR / "processos").mkdir(parents=True, exist_ok=True)
    ROUTERBOX_DIR.mkdir(parents=True, exist_ok=True)


def _local_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now().astimezone()
    return now


def _bounded_worker_int(
    value,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid integer")
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ValueError("integer outside bounds")
    return parsed


def _financeiro_schedule_settings(
    *,
    require_enabled: bool = True,
):
    if require_enabled and not FINANCEIRO_MEDICAO_SCHEDULE_ENABLED:
        return None
    if require_enabled:
        try:
            settings = FinanceiroMedicaoSettings.from_mapping(os.environ)
            if not settings.schedule_enabled:
                raise ValueError("schedule disabled")
        except (OSError, ValueError):
            logger.error(
                "[financeiro_medicao] Agenda desabilitada; "
                "error_code=CONFIG_INVALID."
            )
            return None
        return settings
    try:
        timezone_name = str(FINANCEIRO_MEDICAO_TIMEZONE).strip()
        ZoneInfo(timezone_name)
        schedule_hour = _bounded_worker_int(
            FINANCEIRO_MEDICAO_SCHEDULE_HOUR,
            minimum=0,
            maximum=23,
        )
        schedule_minute = _bounded_worker_int(
            FINANCEIRO_MEDICAO_SCHEDULE_MINUTE,
            minimum=0,
            maximum=59,
        )
        runtime_root = Path(FINANCEIRO_MEDICAO_RUNTIME_ROOT)
        raw_runtime_root = str(FINANCEIRO_MEDICAO_RUNTIME_ROOT)
        if (
            not (
                runtime_root.is_absolute()
                or raw_runtime_root.startswith(("/", "\\"))
            )
            or runtime_root.name != "financeiro_medicao"
        ):
            raise ValueError("invalid runtime root")
    except Exception:
        logger.error(
            "[financeiro_medicao] Agenda desabilitada; "
            "error_code=CONFIG_INVALID."
        )
        return None
    return SimpleNamespace(
        runtime_root=runtime_root,
        schedule_enabled=True,
        schedule_hour=schedule_hour,
        schedule_minute=schedule_minute,
        timezone=timezone_name,
    )


def _next_run_at(now: datetime | None = None) -> datetime:
    now = _local_now(now)
    candidate = now.replace(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _next_multiplica_run_at(now: datetime | None = None) -> datetime:
    now = _local_now(now)
    candidate = now.replace(
        hour=MULTIPLICA_SCHEDULE_HOUR,
        minute=MULTIPLICA_SCHEDULE_MINUTE,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _next_financeiro_medicao_run_at(
    now: datetime | None = None,
) -> datetime:
    settings = _financeiro_schedule_settings(
        require_enabled=False,
    )
    if settings is None:
        raise ValueError("Invalid financeiro schedule.")
    if now is None:
        local_now = datetime.now(ZoneInfo(settings.timezone))
    elif now.tzinfo is None:
        local_now = now
    else:
        local_now = now.astimezone(ZoneInfo(settings.timezone))
    candidate = local_now.replace(
        hour=settings.schedule_hour,
        minute=settings.schedule_minute,
        second=0,
        microsecond=0,
    )
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate


def _run_multiplica_signal_if_present() -> bool:
    claim_lock = MULTIPLICA_SIGNAL_FILE.parent / (
        f".{MULTIPLICA_SIGNAL_FILE.name}.claim.lock"
    )
    try:
        with file_lock(claim_lock, wait_seconds=0):
            return _run_claimed_multiplica_signal()
    except LockUnavailable:
        return False


def _run_claimed_multiplica_signal() -> bool:
    _reconcile_multiplica_claims()
    if not MULTIPLICA_SIGNAL_FILE.exists():
        return False
    claimed_signal = MULTIPLICA_SIGNAL_FILE.with_name(
        f".{MULTIPLICA_SIGNAL_FILE.name}.claimed.{uuid4().hex}"
    )
    try:
        os.replace(MULTIPLICA_SIGNAL_FILE, claimed_signal)
    except FileNotFoundError:
        return False

    logger.info("Signal Multiplica detectado. Executando coleta manual.")
    from flows.multiplica.runner import AlreadyRunning, run_once

    try:
        run_once()
    except AlreadyRunning:
        os.replace(claimed_signal, MULTIPLICA_SIGNAL_FILE)
        logger.info(
            "Chromium ocupado. Signal Multiplica preservado para retry."
        )
        return False
    except BaseException:
        os.replace(claimed_signal, MULTIPLICA_SIGNAL_FILE)
        raise
    claimed_signal.unlink(missing_ok=True)
    return True


def _reconcile_multiplica_claims() -> None:
    parent = MULTIPLICA_SIGNAL_FILE.parent
    if not parent.exists():
        return

    prefix = f".{MULTIPLICA_SIGNAL_FILE.name}.claimed."
    claims = []
    for candidate in parent.iterdir():
        suffix = candidate.name.removeprefix(prefix)
        if (
            candidate.is_file()
            and candidate.name.startswith(prefix)
            and len(suffix) == 32
            and all(character in "0123456789abcdef" for character in suffix)
        ):
            claims.append(candidate)

    for claim in sorted(claims, key=lambda path: path.name):
        if MULTIPLICA_SIGNAL_FILE.exists():
            claim.unlink(missing_ok=True)
        else:
            os.replace(claim, MULTIPLICA_SIGNAL_FILE)


def _request_multiplica_retry() -> None:
    MULTIPLICA_SIGNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = MULTIPLICA_SIGNAL_FILE.with_name(
        f".{MULTIPLICA_SIGNAL_FILE.name}.{uuid4().hex}.tmp"
    )
    descriptor = None
    created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        created = True
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, MULTIPLICA_SIGNAL_FILE)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            temporary.unlink(missing_ok=True)


def _run_scheduled_multiplica() -> bool:
    from flows.multiplica.runner import AlreadyRunning, run_once

    try:
        run_once()
    except AlreadyRunning:
        _request_multiplica_retry()
        logger.info(
            "Chromium ocupado. Coleta Multiplica agendada para retry."
        )
        return False
    return True


def _run_scheduled_financeiro_medicao(
    *,
    scheduled_for: datetime | None = None,
) -> bool:
    settings = _financeiro_schedule_settings()
    if settings is None:
        return False
    now = _local_now()
    if scheduled_for is None:
        scheduled_for = financeiro_schedule.next_event_at(
            now,
            settings,
        )
    financeiro_schedule.request_run(
        settings,
        scheduled_for,
        now=now,
    )
    return bool(
        financeiro_schedule.run_signal_if_due(
            settings,
            now=now,
        )
    )


def _protheus_signal_mode(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw) if raw.strip() else {}
        if payload.get("mode") == "retry-falhos":
            return "retry-falhos"
    except (AttributeError, OSError, ValueError):
        pass
    return "full"


def _protheus_claims() -> list[Path]:
    parent = SIGNAL_FILE.parent
    if not parent.exists():
        return []

    prefix = f".{SIGNAL_FILE.name}.claimed."
    claims = []
    for candidate in parent.iterdir():
        suffix = candidate.name.removeprefix(prefix)
        if (
            candidate.is_file()
            and candidate.name.startswith(prefix)
            and len(suffix) == 32
            and all(character in "0123456789abcdef" for character in suffix)
        ):
            claims.append(candidate)
    return sorted(claims, key=lambda path: path.name)


def _restore_protheus_claim(claimed_signal: Path) -> None:
    _request_protheus_retry(_protheus_signal_mode(claimed_signal))
    claimed_signal.unlink(missing_ok=True)


def _reconcile_protheus_claims() -> None:
    for claimed_signal in _protheus_claims():
        _restore_protheus_claim(claimed_signal)


def _run_protheus_signal_if_present() -> bool:
    claim_lock = SIGNAL_FILE.parent / (
        f".{SIGNAL_FILE.name}.claim.lock"
    )
    try:
        with file_lock(claim_lock, wait_seconds=0):
            _reconcile_protheus_claims()
            if not SIGNAL_FILE.exists():
                return False

            claimed_signal = SIGNAL_FILE.with_name(
                f".{SIGNAL_FILE.name}.claimed.{uuid4().hex}"
            )
            try:
                os.replace(SIGNAL_FILE, claimed_signal)
            except FileNotFoundError:
                return False

            mode = _protheus_signal_mode(claimed_signal)
            logger.info(f"Signal Protheus detectado. mode={mode}")
            try:
                _run_with_auto_retry(mode)
            except BaseException:
                _restore_protheus_claim(claimed_signal)
                raise
            claimed_signal.unlink(missing_ok=True)
            return True
    except LockUnavailable:
        return False


def _request_protheus_retry(mode: str) -> None:
    retry_mode = mode if mode in {"full", "retry-falhos"} else "full"
    SIGNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SIGNAL_FILE.with_name(
        f".{SIGNAL_FILE.name}.{uuid4().hex}.tmp"
    )
    descriptor = None
    created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        created = True
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as stream:
            descriptor = None
            json.dump(
                {"mode": retry_mode},
                stream,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        while True:
            try:
                raw = SIGNAL_FILE.read_text(encoding="utf-8")
            except FileNotFoundError:
                existing_mode = None
            except (OSError, ValueError):
                existing_mode = "full"
            else:
                try:
                    existing_payload = (
                        json.loads(raw) if raw.strip() else {}
                    )
                    existing_mode = (
                        "retry-falhos"
                        if existing_payload.get("mode")
                        == "retry-falhos"
                        else "full"
                    )
                except (AttributeError, ValueError):
                    existing_mode = "full"

            if (
                existing_mode == "full"
                or (
                    existing_mode == "retry-falhos"
                    and retry_mode == "retry-falhos"
                )
            ):
                return

            if existing_mode is None:
                try:
                    os.link(temporary, SIGNAL_FILE)
                    return
                except FileExistsError:
                    continue

            # A full run subsumes any concurrent retry-falhos request, so
            # replacing the weaker signal cannot discard pending work.
            os.replace(temporary, SIGNAL_FILE)
            return
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            temporary.unlink(missing_ok=True)


def _cleanup_run_artifacts() -> None:
    for f in (LOG_FILE, DONE_FILE, READY_FILE):
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass


def _load_technicians_lookup() -> dict[str, str]:
    """Mapeia code → name a partir do technicians.json (pra enriquecer falhas)."""
    try:
        from core.config import PROJECT_ROOT, settings
        path = settings.tecnicos_path
    except Exception:
        path = Path(__file__).resolve().parent / "technicians.json"

    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {item["code"]: item.get("name", item["code"]) for item in data if "code" in item}
    except (OSError, ValueError, KeyError):
        return {}


def _read_checkpoint_summary() -> tuple[int, int, list[dict]]:
    """Lê o checkpoint do dia e retorna (total, ok, falhas[]).

    falhas[] = [{code, name, erro_msg}]
    """
    today = datetime.now().strftime("%Y-%m-%d")
    state_dir = Path(__file__).resolve().parent / "state"
    checkpoint = state_dir / f"checkpoint_{today}.json"

    if not checkpoint.exists():
        return 0, 0, []

    try:
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        items = data.get("items", {})
    except (OSError, ValueError):
        return 0, 0, []

    nomes = _load_technicians_lookup()
    total = len(items)
    ok = sum(1 for it in items.values() if it.get("status") == "sucesso")
    falhas = [
        {
            "code": cod,
            "name": nomes.get(cod, cod),
            "erro_msg": (it.get("erro_msg") or "").strip() or "Sem detalhes",
            "tentativas": it.get("tentativas", 0),
        }
        for cod, it in items.items()
        if it.get("status") not in ("sucesso", "pendente")
    ]
    return total, ok, falhas


def _write_done(
    success: bool,
    message: str,
    started_at: str,
    exit_code: int | None,
    mode: str,
    total: int,
    ok: int,
    falhas: list[dict],
) -> None:
    payload = {
        "success": success,
        "message": message,
        "started_at": started_at,
        "finished_at": _now_iso(),
        "exit_code": exit_code,
        "mode": mode,
        "tecnicos_total": total,
        "tecnicos_ok": ok,
        "tecnicos_falhos": falhas,
    }
    try:
        DONE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        print(f"[worker] erro ao escrever run.done: {exc}", file=sys.stderr)


def _touch_signal_ready() -> None:
    try:
        READY_FILE.touch()
    except OSError as exc:
        print(f"[worker] erro ao criar signal.ready: {exc}", file=sys.stderr)


def _executar_robo(
    mode: str,
) -> tuple[bool, str, int | None, bool]:
    """Dispara main.main e informa se o entrypoint chegou a ser chamado."""
    started_at = _now_iso()
    argv: list[str] = []
    if mode == "retry-falhos":
        argv = ["--retry-falhos"]
        if INCLUDE_DISMISSED:
            argv.append("--incluir-desligados")
    else:
        # Modo full/scheduled: opcionalmente incluir técnicos desligados
        if INCLUDE_DISMISSED:
            argv = ["--incluir-desligados"]

    success = False
    message = ""
    exit_code: int | None = None
    lock_error = None
    sink_id = None
    entrypoint_called = False

    try:
        with file_lock(
            GLOBAL_CHROMIUM_LOCK,
            wait_seconds=CHROMIUM_LOCK_WAIT_SECONDS,
        ):
            _cleanup_run_artifacts()
            sink_id = logger.add(
                LOG_FILE,
                level="INFO",
                encoding="utf-8",
                format=(
                    "{time:HH:mm:ss} | {level: <7} | "
                    "{extra[etapa]:<14} | {message}"
                ),
                enqueue=False,
                filter=lambda r: (
                    r["extra"].setdefault("etapa", "-") or True
                ),
            )
            logger.bind(etapa="worker").info(
                f"== Início (mode={mode}, started_at={started_at}) =="
            )
            from main import main as robo_main

            entrypoint_called = True
            exit_code = robo_main(argv)
        success = exit_code == 0
        if exit_code == 0:
            message = "Todos os técnicos processados com sucesso."
        elif exit_code == 1:
            message = "Concluído com falhas individuais (parciais)."
        elif exit_code == 2:
            message = "Aborto crítico (credenciais ou sessão TOTVS)."
        elif exit_code == 3:
            message = "Erro de configuração (.env / JSON / schema)."
        else:
            message = f"Exit code inesperado: {exit_code}"
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
        success = exit_code == 0
        message = f"sys.exit({exit_code})"
    except LockUnavailable as exc:
        lock_error = exc
        message = "LOCKED"
    except Exception as exc:
        success = False
        message = f"Erro fatal no worker: {exc}"
        logger.bind(etapa="worker").error(message)
        logger.bind(etapa="worker").error(traceback.format_exc())

    if lock_error is not None:
        raise lock_error

    # Guard: main.py may have removed our shared sink via
    # core/log.py's configurar_log() (logger.remove() without args).
    # Re-add the shared sink so the "Fim" line and subsequent logs
    # appear in the Portal D+ tail view.
    if sink_id is None or sink_id not in logger._core.handlers:
        sink_id = logger.add(
            LOG_FILE,
            level="INFO",
            encoding="utf-8",
            format="{time:HH:mm:ss} | {level: <7} | {extra[etapa]:<14} | {message}",
            enqueue=False,
            filter=lambda r: r["extra"].setdefault("etapa", "-") or True,
        )

    logger.bind(etapa="worker").info(f"== Fim (success={success}, exit_code={exit_code}) ==")
    # Sink pode ter sido removido por main.py (loguru.remove() sem id apaga todos).
    # Ignorar erro de "no existing handler" pra não travar o fluxo final.
    try:
        logger.remove(sink_id)
    except ValueError:
        pass
    return success, message, exit_code, entrypoint_called


def _run_once(mode: str = "scheduled") -> None:
    started_at = _now_iso()
    success, message, exit_code, entrypoint_called = _executar_robo(mode)
    if entrypoint_called:
        total, ok, falhas = _read_checkpoint_summary()
    else:
        total, ok, falhas = 0, 0, []

    _write_done(success, message, started_at, exit_code, mode, total, ok, falhas)

    # signal.ready criado se houve AO MENOS algum sucesso
    # (mesmo com falhas parciais — user decide se reprocessa antes de consolidar)
    if ok > 0:
        _touch_signal_ready()
        logger.info(
            f"signal.ready criado. {ok}/{total} OK, {len(falhas)} falhas. "
            "Portal D+ vai mostrar banner."
        )
    else:
        logger.warning(f"Nenhum sucesso ({ok}/{total}). signal.ready NÃO criado.")


def _sleep_until_or_signal(target: datetime) -> str | None:
    """Dorme até target OU até run.signal aparecer.

    Retorna:
        None       → atingiu target (executar scheduled)
        'signal'   → signal detectado antes do target
    """
    while True:
        if SIGNAL_FILE.exists():
            return "signal"
        now = _local_now()
        if target.tzinfo is None:
            now = now.replace(tzinfo=None)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return None
        chunk = min(remaining, float(POLL_INTERVAL_S))
        time.sleep(chunk)


def _run_with_auto_retry(mode: str) -> None:
    """Executa _run_once e, se houver falha total (0 sucessos), re-tenta 1x."""
    try:
        _run_once(mode=mode)
    except LockUnavailable:
        _request_protheus_retry(mode)
        logger.info(
            "Chromium ocupado. Signal Protheus preservado para retry."
        )
        time.sleep(POLL_INTERVAL_S)
        return

    if not AUTO_RETRY:
        return

    # Falha total = signal.ready nao foi criado
    if READY_FILE.exists():
        return  # Houve pelo menos 1 sucesso

    if mode == "retry-falhos":
        return  # Nao faz retry de um retry

    logger.warning(
        f"Falha total detectada (0 sucessos). Auto-retry habilitado — "
        f"re-tentando em {RETRY_DELAY_S}s ({RETRY_DELAY_S//60} min)..."
    )
    time.sleep(RETRY_DELAY_S)

    try:
        _run_once(mode=mode)
    except LockUnavailable:
        _request_protheus_retry(mode)
        logger.info(
            "Chromium ocupado. Signal Protheus preservado para retry."
        )
        time.sleep(POLL_INTERVAL_S)
        return

    if not READY_FILE.exists():
        logger.error(
            f"Auto-retry tambem falhou (0 sucessos). "
            f"Proxima tentativa somente no horario agendado de amanha."
        )


def _run_routerbox_backlog() -> None:
    """Executa o fluxo RouterBox Backlog e grava artifact de resultado."""
    logger.info("[routerbox] Iniciando download + consolidação do backlog RouterBox.")
    ROUTERBOX_DIR.mkdir(parents=True, exist_ok=True)

    # NÃO deletar done anterior — portal precisa dele durante a execução.

    started_at = _now_iso()
    try:
        from flows.routerbox_backlog import run_routerbox_backlog
        with file_lock(
            GLOBAL_CHROMIUM_LOCK,
            wait_seconds=CHROMIUM_LOCK_WAIT_SECONDS,
        ):
            exit_code = run_routerbox_backlog()
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
    except Exception as exc:
        logger.error(f"[routerbox] Erro fatal: {exc}")
        logger.error(traceback.format_exc())
        exit_code = 2

    success = exit_code == 0
    message = {
        0: "RouterBox backlog download + consolidação OK",
        1: "RouterBox backlog: download parcial",
        2: "RouterBox backlog: falha crítica",
        3: "RouterBox backlog: erro de configuração",
    }.get(exit_code, f"RouterBox backlog: exit code {exit_code}")

    payload = {
        "success": success,
        "message": message,
        "started_at": started_at,
        "finished_at": _now_iso(),
        "exit_code": exit_code,
        "mode": "routerbox-backlog",
    }

    # Tentar enriquecer com dados do manifest consolidado
    today = datetime.now().strftime("%Y-%m-%d")
    manifest_path = ROUTERBOX_DIR / f"manifest-{today}.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload.update({
                "linhas_total": manifest.get("linhas_total"),
                "linhas_acerta": manifest.get("linhas_acerta"),
                "linhas_loga": manifest.get("linhas_loga"),
                "ultima_data_ab": manifest.get("ultima_data_ab"),
                "arquivo": manifest.get("arquivo"),
                "fresh_downloads": manifest.get("fresh_downloads"),
                "fallback_downloads": manifest.get("fallback_downloads"),
                "used_fallback": manifest.get("used_fallback"),
                "source_mtimes": manifest.get("source_mtimes"),
                "source_mtime_min": manifest.get("source_mtime_min"),
                "source_mtime_max": manifest.get("source_mtime_max"),
            })
        except (OSError, ValueError):
            pass

    try:
        ROUTERBOX_DONE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.error(f"[routerbox] Erro ao escrever {ROUTERBOX_DONE_FILE}: {exc}")

    logger.info(f"[routerbox] Fim: success={success} exit_code={exit_code}")


def _next_routerbox_run_at(now: datetime | None = None) -> datetime:
    """Retorna o próximo horário de execução do RouterBox (dentro da janela configurada)."""
    now = _local_now(now)
    interval = ROUTERBOX_INTERVAL_MIN
    minutes_today = now.hour * 60 + now.minute

    # Se antes da janela, agendar para o início
    if minutes_today < ROUTERBOX_START_MINUTES:
        candidate = now.replace(hour=ROUTERBOX_START_MINUTES // 60, minute=ROUTERBOX_START_MINUTES % 60, second=0, microsecond=0)
        return candidate

    # Se depois da janela, agendar para o início do dia seguinte
    if minutes_today >= ROUTERBOX_END_MINUTES:
        candidate = (now + timedelta(days=1)).replace(hour=ROUTERBOX_START_MINUTES // 60, minute=ROUTERBOX_START_MINUTES % 60, second=0, microsecond=0)
        return candidate

    # Dentro da janela: próximo slot alinhado ao intervalo
    next_slot = ((minutes_today // interval) + 1) * interval

    # Se o próximo slot cair no fim ou fora da janela, pula para o dia seguinte
    if next_slot >= ROUTERBOX_END_MINUTES:
        candidate = (now + timedelta(days=1)).replace(hour=ROUTERBOX_START_MINUTES // 60, minute=ROUTERBOX_START_MINUTES % 60, second=0, microsecond=0)
        return candidate

    candidate = now.replace(hour=next_slot // 60, minute=next_slot % 60, second=0, microsecond=0)
    return candidate


def _scheduled_events(now: datetime | None = None) -> list[tuple[str, datetime]]:
    now = _local_now(now)
    events: list[tuple[str, datetime]] = []
    if PROTHEUS_ENABLED:
        events.append(("protheus", _next_run_at(now)))
    if ROUTERBOX_ENABLED:
        events.append(("routerbox", _next_routerbox_run_at(now)))
    if MULTIPLICA_SCHEDULE_ENABLED:
        events.append(("multiplica", _next_multiplica_run_at(now)))
    financeiro_settings = _financeiro_schedule_settings()
    if financeiro_settings is not None:
        events.append(
            (
                "financeiro_medicao",
                financeiro_schedule.next_event_at(
                    now,
                    financeiro_settings,
                ),
            )
        )
    return sorted(events, key=lambda event: event[1])


def _advance_scheduled_event(
    events: dict[str, datetime],
    name: str,
) -> None:
    now = _local_now()
    if name == "protheus" and PROTHEUS_ENABLED:
        events[name] = _next_run_at(now)
    elif name == "routerbox" and ROUTERBOX_ENABLED:
        events[name] = _next_routerbox_run_at(now)
    elif name == "multiplica" and MULTIPLICA_SCHEDULE_ENABLED:
        events[name] = _next_multiplica_run_at(now)
    elif name == "financeiro_medicao":
        financeiro_settings = _financeiro_schedule_settings()
        if financeiro_settings is None:
            events.pop(name, None)
        else:
            events[name] = financeiro_schedule.next_event_at(
                now,
                financeiro_settings,
            )
    else:
        events.pop(name, None)


def loop_forever() -> None:
    _ensure_dirs()
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="[worker] {time:YYYY-MM-DD HH:mm:ss} | {message}")

    logger.info(
        f"Worker iniciado. pipeline_dir={DATA_PIPELINE_DIR} "
        f"scheduled={SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} "
        f"run_on_start={RUN_ON_START} include_dismissed={INCLUDE_DISMISSED} "
        f"poll={POLL_INTERVAL_S}s"
    )
    logger.info(
        f"RouterBox backlog: enabled={ROUTERBOX_ENABLED} "
        f"interval={ROUTERBOX_INTERVAL_MIN}min "
        f"on_start={ROUTERBOX_ON_START} dir={ROUTERBOX_DIR}"
    )
    logger.info(
        f"Multiplica: scheduled={MULTIPLICA_SCHEDULE_ENABLED} "
        f"time={MULTIPLICA_SCHEDULE_HOUR:02d}:{MULTIPLICA_SCHEDULE_MINUTE:02d} "
        f"signal={MULTIPLICA_SIGNAL_FILE}"
    )
    logger.info(
        "Financeiro medição: "
        f"scheduled={FINANCEIRO_MEDICAO_SCHEDULE_ENABLED} "
        f"time={FINANCEIRO_MEDICAO_SCHEDULE_HOUR}:"
        f"{FINANCEIRO_MEDICAO_SCHEDULE_MINUTE} "
        f"timezone={FINANCEIRO_MEDICAO_TIMEZONE}"
    )

    if PROTHEUS_ENABLED and RUN_ON_START:
        logger.info("ROBOT_RUN_ON_START=true → executando imediatamente.")
        try:
            _run_with_auto_retry(mode="scheduled")
        except Exception as exc:
            logger.error(f"Erro no run_on_start: {exc}")
            logger.error(traceback.format_exc())

    if ROUTERBOX_ENABLED and ROUTERBOX_ON_START:
        logger.info("ROUTERBOX_RUN_ON_START=true → executando RouterBox imediatamente.")
        try:
            _run_routerbox_backlog()
        except Exception as exc:
            logger.error(f"Erro no RouterBox run_on_start: {exc}")
            logger.error(traceback.format_exc())

    scheduled_events = dict(_scheduled_events())
    while True:
        try:
            if _run_multiplica_signal_if_present():
                continue

            # Determinar qual scheduler dispara primeiro
            if not scheduled_events:
                logger.warning("Nenhuma automação habilitada; aguardando configuração.")
                time.sleep(POLL_INTERVAL_S)
                scheduled_events.update(_scheduled_events())
                continue

            # Ordenar por horário
            next_name, next_time = min(
                scheduled_events.items(),
                key=lambda event: event[1],
            )

            # Dormir até o próximo evento, mas checar signal a cada POLL_INTERVAL_S
            remaining = (next_time - _local_now()).total_seconds()
            logger.info(
                f"Próximo evento: {next_name} em {int(remaining)}s "
                f"({next_time.strftime('%H:%M:%S')})"
            )

            while remaining > 0:
                if _run_multiplica_signal_if_present():
                    break

                # Checar signal do Protheus
                if (
                    PROTHEUS_ENABLED
                    and _run_protheus_signal_if_present()
                ):
                    break

                chunk = min(remaining, float(POLL_INTERVAL_S))
                time.sleep(chunk)
                remaining = (next_time - _local_now()).total_seconds()

            # Executar o evento que venceu
            if remaining <= 0:
                if next_name == "protheus":
                    logger.info("Horário-alvo atingido. Disparando robô Protheus (mode=scheduled).")
                    _run_with_auto_retry(mode="scheduled")
                elif next_name == "routerbox":
                    _run_routerbox_backlog()
                elif next_name == "multiplica":
                    _run_scheduled_multiplica()
                elif next_name == "financeiro_medicao":
                    _run_scheduled_financeiro_medicao(
                        scheduled_for=next_time,
                    )
                _advance_scheduled_event(
                    scheduled_events,
                    next_name,
                )

        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt recebido. Encerrando.")
            break
        except Exception as exc:
            logger.error(f"Erro inesperado no loop: {exc}")
            logger.error(traceback.format_exc())
            time.sleep(60)


if __name__ == "__main__":
    loop_forever()
