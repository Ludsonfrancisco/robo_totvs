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
POLL_INTERVAL_S = int(os.environ.get("WORKER_POLL_INTERVAL", "5"))

SIGNAL_FILE = DATA_PIPELINE_DIR / "run.signal"
LOG_FILE = DATA_PIPELINE_DIR / "run.log"
DONE_FILE = DATA_PIPELINE_DIR / "run.done"
READY_FILE = DATA_PIPELINE_DIR / "signal.ready"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _ensure_dirs() -> None:
    DATA_PIPELINE_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_PIPELINE_DIR / "entrada").mkdir(parents=True, exist_ok=True)
    (DATA_PIPELINE_DIR / "processos").mkdir(parents=True, exist_ok=True)


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

    logger.bind(etapa="worker").info(f"== Fim (success={success}, exit_code={exit_code}) ==")
    logger.remove(sink_id)
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


def loop_forever() -> None:
    _ensure_dirs()
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="[worker] {time:YYYY-MM-DD HH:mm:ss} | {message}")

    logger.info(
        f"Worker iniciado. pipeline_dir={DATA_PIPELINE_DIR} "
        f"scheduled={SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} "
        f"run_on_start={RUN_ON_START} poll={POLL_INTERVAL_S}s"
    )

    if RUN_ON_START:
        logger.info("ROBOT_RUN_ON_START=true → executando imediatamente.")
        try:
            _run_once(mode="scheduled")
        except Exception as exc:
            logger.error(f"Erro no run_on_start: {exc}")
            logger.error(traceback.format_exc())

    while True:
        try:
            next_run = _next_run_at()
            logger.info(f"Aguardando próxima execução agendada: {next_run.isoformat(timespec='seconds')}")
            trigger = _sleep_until_or_signal(next_run)

            if trigger == "signal":
                payload = _consume_signal() or {}
                mode = payload.get("mode", "full")
                logger.info(f"Signal detectado. Payload={payload} mode={mode}")
                _run_once(mode=mode)
            else:
                logger.info("Horário-alvo atingido. Disparando robô (mode=scheduled).")
                _run_once(mode="scheduled")
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt recebido. Encerrando.")
            break
        except Exception as exc:
            logger.error(f"Erro inesperado no loop: {exc}")
            logger.error(traceback.format_exc())
            time.sleep(60)


if __name__ == "__main__":
    loop_forever()
