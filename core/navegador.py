from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from core.config import settings
from core.log import log


@contextmanager
def iniciar_navegador() -> Iterator[tuple[Browser, BrowserContext, Page]]:
    log.bind(etapa="navegador").info(
        f"iniciando chromium (headless={settings.HEADLESS}, "
        f"viewport={settings.VIEWPORT_W}x{settings.VIEWPORT_H})"
    )
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.HEADLESS)
        context = browser.new_context(
            viewport={"width": settings.VIEWPORT_W, "height": settings.VIEWPORT_H},
            accept_downloads=True,
            ignore_https_errors=True,
        )
        page = context.new_page()
        try:
            yield browser, context, page
        finally:
            log.bind(etapa="navegador").info("fechando navegador")
            context.close()
            browser.close()


def tirar_screenshot(page: Page, etapa: str = "screenshot", evidencia: bool = False) -> Path:
    base = settings.logs_dir / "evidencias" if evidencia else settings.logs_dir
    base.mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.now():%Y-%m-%d_%H%M%S}_{etapa}.png"
    path = base / filename
    page.screenshot(path=str(path), full_page=False)
    log.bind(etapa=etapa).debug(f"screenshot salvo em {path}")
    return path
