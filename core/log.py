import sys
from datetime import datetime

from loguru import logger

from core.config import settings

_configured = False
_LOG_SINKS: list[int] = []  # track sinks WE add, so we only remove ours


def configurar_log() -> "logger":
    global _configured
    if _configured:
        return logger

    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    # Only remove sinks that log.py itself owns — never use logger.remove()
    # with no arguments, as that destroys sinks added by external callers
    # (e.g. worker.py's shared volume sink at /app/data_pipeline/run.log).
    for sid in list(_LOG_SINKS):
        try:
            logger.remove(sid)
        except ValueError:
            pass
    _LOG_SINKS.clear()

    # On first call, remove loguru's default stderr handler to avoid duplicates.
    # It's always the very first handler added by loguru at import time.
    # We identify it by checking if the sink writes to stderr (the default).
    # Skip this if there's only one handler (could be ours from a retry).
    if len(logger._core.handlers) > 0 and not _LOG_SINKS:
        for sid, h in list(logger._core.handlers.items()):
            sink_str = str(getattr(h, "_sink", ""))
            if "stderr" in sink_str.lower() or "stdout" in sink_str.lower():
                # Verify it's NOT a sink we already manage
                if sid not in _LOG_SINKS:
                    try:
                        logger.remove(sid)
                    except ValueError:
                        pass
                    break  # only remove the default

    # Stderr sink (colorized, for container logs)
    sid = logger.add(
        sys.stderr,
        level="INFO",
        colorize=True,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <7}</level> | "
            "<cyan>{extra[etapa]}</cyan> | "
            "<level>{message}</level>"
        ),
        filter=lambda record: record["extra"].setdefault("etapa", "-") or True,
    )
    _LOG_SINKS.append(sid)

    # File sink (detailed, for debugging)
    log_file = settings.logs_dir / f"run-{datetime.now():%Y-%m-%d-%H%M%S}.log"
    sid = logger.add(
        log_file,
        level="DEBUG",
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        format=(
            "{time:YYYY-MM-DDTHH:mm:ss.SSS} | {level: <7} | "
            "{extra[etapa]} | {extra[tecnico]} | {message}"
        ),
        filter=lambda record: (
            record["extra"].setdefault("etapa", "-"),
            record["extra"].setdefault("tecnico", "-"),
        )
        and True,
    )
    _LOG_SINKS.append(sid)

    _configured = True
    return logger


log = configurar_log()
