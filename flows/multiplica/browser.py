from contextlib import contextmanager
import os

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .loga import CollectionError


def _is_authenticated(page):
    return (
        page.title().strip() == "Indicadores SLA e Qualidade"
        and page.locator('input[type="password"]').count() == 0
    )


def _ensure_authenticated(page, context, settings, environ):
    page.goto(settings.loga_url, wait_until="networkidle")
    if _is_authenticated(page):
        return

    username_input = page.get_by_label("E-Mail", exact=True)
    password_input = page.get_by_label("Senha", exact=True)
    if username_input.count() != 1 or password_input.count() != 1:
        raise CollectionError("AUTH_EXPIRED")

    username = str(environ.get("MULTIPLICA_LOGA_USER", "")).strip()
    password = str(environ.get("MULTIPLICA_LOGA_PASSWORD", ""))
    if not username or not password:
        raise CollectionError("AUTH_EXPIRED")

    try:
        username_input.fill(username)
        password_input.fill(password)
        page.get_by_role(
            "button",
            name="Entrar",
            exact=True,
        ).click()
        page.locator('input[type="password"]').wait_for(
            state="detached",
            timeout=30_000,
        )
        page.wait_for_load_state("networkidle")
    except PlaywrightTimeoutError as exc:
        raise CollectionError("AUTH_EXPIRED") from exc

    if not _is_authenticated(page):
        raise CollectionError("AUTH_EXPIRED")

    temporary = (
        settings.runtime_root
        / "runtime"
        / "loga-storage-state.json.tmp"
    )
    context.storage_state(path=str(temporary))
    os.chmod(temporary, 0o600)
    os.replace(temporary, settings.storage_state_path)
    os.chmod(settings.storage_state_path, 0o600)


@contextmanager
def authenticated_page(settings):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chrome")
        context_options = {}
        if settings.storage_state_path.is_file():
            context_options["storage_state"] = str(
                settings.storage_state_path
            )
        context = browser.new_context(**context_options)
        try:
            page = context.new_page()
            _ensure_authenticated(page, context, settings, os.environ)
            yield page
        finally:
            context.close()
            browser.close()
