"""robo-totvs — entrypoint CLI.

Sprint 1: smoke test. Abre Protheus, espera 5s, tira screenshot, fecha.
"""

import time

from core.config import settings
from core.log import log
from core.navegador import iniciar_navegador, tirar_screenshot


def main() -> int:
    log.bind(etapa="boot").info(f"alvo: {settings.PROTHEUS_URL}")

    with iniciar_navegador() as (_, _, page):
        log.bind(etapa="boot").info("abrindo Protheus...")
        page.goto(settings.PROTHEUS_URL, wait_until="domcontentloaded")
        time.sleep(5)
        tirar_screenshot(page, etapa="boot")
        log.bind(etapa="boot").success("smoke test concluído")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
