import sys
from datetime import datetime

from loguru import logger

from core.config import settings

_configured = False


def configurar_log() -> "logger":
    global _configured
    if _configured:
        return logger

    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    logger.add(
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

    log_file = settings.logs_dir / f"run-{datetime.now():%Y-%m-%d-%H%M%S}.log"
    logger.add(
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

    _configured = True
    return logger


log = configurar_log()
