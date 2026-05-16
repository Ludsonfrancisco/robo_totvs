"""Worker persistente do robô TOTVS.

Roda em loop dentro do container. A cada `POLL_INTERVAL_S` segundos
checa se existe `run.signal` no volume compartilhado `DATA_PIPELINE_DIR`.
Se sim, dispara `main.main()` e reporta progresso/resultado nos arquivos:

    <DATA_PIPELINE_DIR>/
    ├── run.signal   ← Django cria (consumido aqui)
    ├── run.log      ← sink loguru extra criado aqui
    ├── run.done     ← criado aqui ao final (JSON)
    └── signal.ready ← trigger pro Portal D+ consolidar (criado se sucesso)

Variáveis de ambiente:
    DATA_PIPELINE_DIR     default: /app/data_pipeline
    WORKER_POLL_INTERVAL  default: 5 (segundos)
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from loguru import logger

DATA_PIPELINE_DIR = Path(os.environ.get("DATA_PIPELINE_DIR", "/app/data_pipeline"))
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


def _consume_signal() -> dict | None:
    """Lê e remove o run.signal. Retorna payload (ou {} se vazio) ou None."""
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
    for f in (LOG_FILE, DONE_FILE):
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass


def _write_done(success: bool, message: str, started_at: str, exit_code: int | None) -> None:
    payload = {
        "success": success,
        "message": message,
        "started_at": started_at,
        "finished_at": _now_iso(),
        "exit_code": exit_code,
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


def _executar_robo(payload: dict) -> tuple[bool, str, int | None]:
    """Adiciona sink loguru pro run.log, dispara main.main() e captura resultado."""
    started_at = _now_iso()
    requested_by = payload.get("requested_by", "?")

    # Adiciona sink extra apontando pro arquivo lido pela UI Django.
    # Format simples (sem cores) — UI já color-codeia por keyword.
    sink_id = logger.add(
        LOG_FILE,
        level="INFO",
        encoding="utf-8",
        format="{time:HH:mm:ss} | {level: <7} | {extra[etapa]:<14} | {message}",
        enqueue=False,
        filter=lambda r: r["extra"].setdefault("etapa", "-") or True,
    )

    logger.bind(etapa="worker").info(
        f"== Início (requested_by={requested_by}, started_at={started_at}) =="
    )

    success = False
    message = ""
    exit_code: int | None = None

    try:
        # Import tardio: garante que loguru extra sink já tá configurado
        # antes de main.py rodar (todas as linhas vão pro run.log também).
        from main import main as robo_main

        exit_code = robo_main([])
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
        # main.py pode chamar sys.exit() em paths legados
        exit_code = int(exc.code or 0)
        success = exit_code == 0
        message = f"sys.exit({exit_code})"
    except Exception as exc:
        success = False
        message = f"Erro fatal no worker: {exc}"
        logger.bind(etapa="worker").error(message)
        logger.bind(etapa="worker").error(traceback.format_exc())

    logger.bind(etapa="worker").info(
        f"== Fim (success={success}, exit_code={exit_code}) =="
    )

    logger.remove(sink_id)
    return success, message, exit_code


def loop_forever() -> None:
    _ensure_dirs()
    logger.remove()  # remove sink default do loguru pra não duplicar com core.log
    logger.add(sys.stderr, level="INFO", format="[worker] {time:HH:mm:ss} {message}")
    logger.info(f"Worker iniciado. pipeline_dir={DATA_PIPELINE_DIR}, poll={POLL_INTERVAL_S}s")

    while True:
        try:
            payload = _consume_signal()
            if payload is not None:
                logger.info(f"Signal detectado. Payload={payload}")
                _cleanup_run_artifacts()
                started_at = _now_iso()
                success, message, exit_code = _executar_robo(payload)
                _write_done(success, message, started_at, exit_code)
                if success:
                    _touch_signal_ready()
                    logger.info("Signal.ready criado — Portal D+ vai consolidar.")
                else:
                    logger.warning(f"Robô falhou: {message}. Signal.ready NÃO criado.")
        except Exception as exc:
            logger.error(f"Erro no loop principal: {exc}")
            logger.error(traceback.format_exc())

        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    loop_forever()
