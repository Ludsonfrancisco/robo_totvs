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

from loguru import logger

DATA_PIPELINE_DIR = Path(os.environ.get("DATA_PIPELINE_DIR", "/app/data_pipeline"))
SCHEDULE_HOUR = int(os.environ.get("ROBOT_SCHEDULE_HOUR", "6"))
SCHEDULE_MINUTE = int(os.environ.get("ROBOT_SCHEDULE_MINUTE", "0"))
RUN_ON_START = os.environ.get("ROBOT_RUN_ON_START", "false").lower() in ("1", "true", "yes")
INCLUDE_DISMISSED = os.environ.get("ROBOT_INCLUDE_DISMISSED", "false").lower() in ("1", "true", "yes")
AUTO_RETRY = os.environ.get("ROBOT_AUTO_RETRY", "true").lower() in ("1", "true", "yes")
RETRY_DELAY_S = int(os.environ.get("ROBOT_RETRY_DELAY", "300"))
POLL_INTERVAL_S = int(os.environ.get("WORKER_POLL_INTERVAL", "5"))

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

# RouterBox Backlog artifacts
ROUTERBOX_DIR = Path(os.environ.get("ROUTERBOX_OUTPUT_DIR", "/app/data_pipeline/routerbox_backlog"))
ROUTERBOX_DONE_FILE = ROUTERBOX_DIR / "run_routerbox.done"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _ensure_dirs() -> None:
    DATA_PIPELINE_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_PIPELINE_DIR / "entrada").mkdir(parents=True, exist_ok=True)
    (DATA_PIPELINE_DIR / "processos").mkdir(parents=True, exist_ok=True)
    ROUTERBOX_DIR.mkdir(parents=True, exist_ok=True)


def _next_run_at(now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    candidate = now.replace(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _consume_signal() -> dict | None:
    """Lê e remove run.signal. Retorna payload (dict) ou None se não existir."""
    if not SIGNAL_FILE.exists():
        return None
    try:
        raw = SIGNAL_FILE.read_text(encoding="utf-8")
        payload = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        payload = {}
    try:
        SIGNAL_FILE.unlink()
    except OSError:
        pass
    return payload


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


def _executar_robo(mode: str) -> tuple[bool, str, int | None]:
    """Adiciona sink loguru, dispara main.main(argv conforme mode), retorna resultado."""
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

    sink_id = logger.add(
        LOG_FILE,
        level="INFO",
        encoding="utf-8",
        format="{time:HH:mm:ss} | {level: <7} | {extra[etapa]:<14} | {message}",
        enqueue=False,
        filter=lambda r: r["extra"].setdefault("etapa", "-") or True,
    )

    logger.bind(etapa="worker").info(f"== Início (mode={mode}, started_at={started_at}) ==")

    success = False
    message = ""
    exit_code: int | None = None

    try:
        from main import main as robo_main

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
    except Exception as exc:
        success = False
        message = f"Erro fatal no worker: {exc}"
        logger.bind(etapa="worker").error(message)
        logger.bind(etapa="worker").error(traceback.format_exc())

    # Guard: main.py may have removed our shared sink via
    # core/log.py's configurar_log() (logger.remove() without args).
    # Re-add the shared sink so the "Fim" line and subsequent logs
    # appear in the Portal D+ tail view.
    if sink_id not in logger._core.handlers:
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
    return success, message, exit_code


def _run_once(mode: str = "scheduled") -> None:
    _cleanup_run_artifacts()
    started_at = _now_iso()
    success, message, exit_code = _executar_robo(mode)
    total, ok, falhas = _read_checkpoint_summary()

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
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            return None
        chunk = min(remaining, float(POLL_INTERVAL_S))
        time.sleep(chunk)


def _run_with_auto_retry(mode: str) -> None:
    """Executa _run_once e, se houver falha total (0 sucessos), re-tenta 1x."""
    _run_once(mode=mode)

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

    # Limpa artifacts antes do retry
    _cleanup_run_artifacts()
    _run_once(mode=mode)

    if not READY_FILE.exists():
        logger.error(
            f"Auto-retry tambem falhou (0 sucessos). "
            f"Proxima tentativa somente no horario agendado de amanha."
        )


def _run_routerbox_backlog() -> None:
    """Executa o fluxo RouterBox Backlog e grava artifact de resultado."""
    logger.info("[routerbox] Iniciando download + consolidação do backlog RouterBox.")
    ROUTERBOX_DIR.mkdir(parents=True, exist_ok=True)

    # Cleanup do done anterior
    if ROUTERBOX_DONE_FILE.exists():
        try:
            ROUTERBOX_DONE_FILE.unlink()
        except OSError:
            pass

    started_at = _now_iso()
    try:
        from flows.routerbox_backlog import run_routerbox_backlog
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
    now = now or datetime.now()
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

    if RUN_ON_START:
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

    while True:
        try:
            # Determinar qual scheduler dispara primeiro
            next_protheus = _next_run_at()
            events = [("protheus", next_protheus)]

            if ROUTERBOX_ENABLED:
                next_routerbox = _next_routerbox_run_at()
                events.append(("routerbox", next_routerbox))
            else:
                next_routerbox = None

            # Ordenar por horário
            events.sort(key=lambda e: e[1])
            next_name, next_time = events[0]

            # Dormir até o próximo evento, mas checar signal a cada POLL_INTERVAL_S
            remaining = (next_time - datetime.now()).total_seconds()
            logger.info(
                f"Próximo evento: {next_name} em {int(remaining)}s "
                f"({next_time.strftime('%H:%M:%S')})"
            )

            while remaining > 0:
                # Checar signal do Protheus
                if SIGNAL_FILE.exists():
                    payload = _consume_signal() or {}
                    mode = payload.get("mode", "full")
                    logger.info(f"Signal detectado. Payload={payload} mode={mode}")
                    _run_with_auto_retry(mode=mode)
                    break

                # Checar se RouterBox deve disparar antes do Protheus
                if ROUTERBOX_ENABLED:
                    rb_remaining = (_next_routerbox_run_at() - datetime.now()).total_seconds()
                    if rb_remaining <= 0:
                        _run_routerbox_backlog()
                        next_routerbox = _next_routerbox_run_at()
                        break

                chunk = min(remaining, float(POLL_INTERVAL_S))
                time.sleep(chunk)
                remaining = (next_time - datetime.now()).total_seconds()

            # Executar o evento que venceu
            if remaining <= 0:
                if next_name == "protheus":
                    logger.info("Horário-alvo atingido. Disparando robô Protheus (mode=scheduled).")
                    _run_with_auto_retry(mode="scheduled")
                elif next_name == "routerbox":
                    _run_routerbox_backlog()

        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt recebido. Encerrando.")
            break
        except Exception as exc:
            logger.error(f"Erro inesperado no loop: {exc}")
            logger.error(traceback.format_exc())
            time.sleep(60)


if __name__ == "__main__":
    loop_forever()
