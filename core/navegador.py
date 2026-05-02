from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from core.config import settings
from core.log import log


@contextmanager
def iniciar_navegador() -> Iterator[tuple[Optional[Browser], BrowserContext, Page]]:
    user_data_dir = settings.browser_user_data_dir
    user_data_dir.mkdir(parents=True, exist_ok=True)
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)

    log.bind(etapa="navegador").info(
        f"iniciando {settings.BROWSER_CHANNEL} (persistent_context, "
        f"headless={settings.HEADLESS}, "
        f"viewport={settings.VIEWPORT_W}x{settings.VIEWPORT_H}, "
        f"user_data_dir={user_data_dir})"
    )

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--no-first-run",
        "--disable-features=IsolateOrigins,site-per-process",
    ]

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            channel=settings.BROWSER_CHANNEL,
            headless=settings.HEADLESS,
            viewport={"width": settings.VIEWPORT_W, "height": settings.VIEWPORT_H},
            accept_downloads=True,
            ignore_https_errors=True,
            downloads_path=str(settings.downloads_dir),
            args=launch_args,
        )

        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = context.pages[0] if context.pages else context.new_page()
        try:
            yield None, context, page
        finally:
            log.bind(etapa="navegador").info("fechando navegador")
            try:
                context.close()
            except Exception as e:
                log.bind(etapa="navegador").debug(f"close ignorado: {e}")


def tirar_screenshot(page: Page, etapa: str = "screenshot", evidencia: bool = False) -> Path:
    base = settings.logs_dir / "evidencias" if evidencia else settings.logs_dir
    base.mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.now():%Y-%m-%d_%H%M%S}_{etapa}.png"
    path = base / filename
    page.screenshot(path=str(path), full_page=False)
    log.bind(etapa=etapa).debug(f"screenshot salvo em {path}")
    return path
