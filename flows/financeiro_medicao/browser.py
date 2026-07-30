from contextlib import contextmanager
import os
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .loga import AUTHENTICATED_TITLE, CollectionError


def _is_authenticated(page) -> bool:
    return (
        page.title() == AUTHENTICATED_TITLE
        and urlsplit(page.url).path == "/medicao_pagamento"
        and page.locator('input[type="password"]').count() == 0
    )


def _save_storage_state(context, storage_state_path) -> None:
    temporary = storage_state_path.with_name(
        f".{storage_state_path.name}.{uuid4().hex}.tmp"
    )
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        os.close(descriptor)
        descriptor = None
        os.chmod(temporary, 0o600)
        context.storage_state(path=str(temporary))
        os.chmod(temporary, 0o600)
        with temporary.open("r+b") as state_file:
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary, storage_state_path)
        os.chmod(storage_state_path, 0o600)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _ensure_authenticated(page, context, settings) -> None:
    try:
        page.goto(settings.loga_url, wait_until="networkidle")
    except PlaywrightTimeoutError as exc:
        raise CollectionError("AUTH_EXPIRED") from exc

    if _is_authenticated(page):
        return

    username_input = page.get_by_label("E-Mail", exact=True)
    password_input = page.get_by_label("Senha", exact=True)
    login_button = page.get_by_role(
        "button",
        name="Entrar",
        exact=True,
    )
    if (
        username_input.count() != 1
        or password_input.count() != 1
        or login_button.count() != 1
        or not str(settings.username).strip()
        or not str(settings.password)
    ):
        raise CollectionError("AUTH_EXPIRED")

    try:
        username_input.fill(settings.username)
        password_input.fill(settings.password)
        login_button.click()
        page.locator('input[type="password"]').wait_for(
            state="detached",
            timeout=30_000,
        )
        page.wait_for_load_state("networkidle", timeout=30_000)
        page.goto(settings.loga_url, wait_until="networkidle")
    except PlaywrightTimeoutError as exc:
        raise CollectionError("AUTH_EXPIRED") from exc

    if not _is_authenticated(page):
        raise CollectionError("AUTH_EXPIRED")

    _save_storage_state(context, settings.storage_state_path)


@contextmanager
def authenticated_page(settings):
    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = playwright.chromium.launch(
                headless=True,
                channel="chrome",
            )
            context_options = {}
            if settings.storage_state_path.is_file():
                context_options["storage_state"] = str(
                    settings.storage_state_path
                )
            context = browser.new_context(**context_options)
            page = context.new_page()
            _ensure_authenticated(page, context, settings)
            yield page
        finally:
            try:
                if context is not None:
                    context.close()
            finally:
                if browser is not None:
                    browser.close()
