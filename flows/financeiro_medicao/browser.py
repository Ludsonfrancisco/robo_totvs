from contextlib import contextmanager
import os
import time
from uuid import uuid4

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .loga import (
    AUTHENTICATED_TITLE,
    CollectionError,
    _same_configured_page,
)


_CLEANUP_DELAYS = (0, 0.01, 0.05)


def _is_posix_runtime() -> bool:
    return os.name == "posix"


def _require_posix_runtime() -> None:
    if not _is_posix_runtime():
        raise CollectionError("UNSUPPORTED_PLATFORM")


def _is_authenticated(page, settings) -> bool:
    return (
        page.title() == AUTHENTICATED_TITLE
        and _same_configured_page(page.url, settings.loga_url)
        and page.locator('input[type="password"]').count() == 0
    )


def _cleanup_sensitive_temp(temporary, code: str) -> None:
    for delay in _CLEANUP_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            temporary.unlink(missing_ok=True)
            return
        except OSError:
            continue
    raise CollectionError(code)


def _save_storage_state(context, storage_state_path) -> None:
    _require_posix_runtime()
    temporary = storage_state_path.with_name(
        f".{storage_state_path.name}.{uuid4().hex}.tmp"
    )
    descriptor = None
    temporary_created = False
    primary_error = None
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        temporary_created = True
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
    except CollectionError as exc:
        primary_error = exc
    except Exception:
        primary_error = CollectionError("AUTH_STATE_FAILED")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                if primary_error is None:
                    primary_error = CollectionError(
                        "AUTH_STATE_FAILED"
                    )

    cleanup_error = None
    if temporary_created:
        try:
            _cleanup_sensitive_temp(
                temporary,
                "AUTH_STATE_CLEANUP_FAILED",
            )
        except CollectionError as exc:
            cleanup_error = exc

    if primary_error is not None:
        if cleanup_error is not None:
            raise primary_error from cleanup_error
        raise primary_error from None
    if cleanup_error is not None:
        raise CollectionError("AUTH_STATE_FAILED") from cleanup_error


def _ensure_authenticated(page, context, settings) -> None:
    try:
        page.goto(settings.loga_url, wait_until="networkidle")
        if _is_authenticated(page, settings):
            return
    except PlaywrightTimeoutError as exc:
        raise CollectionError("AUTH_EXPIRED") from exc

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
        authenticated = _is_authenticated(page, settings)
    except PlaywrightTimeoutError as exc:
        raise CollectionError("AUTH_EXPIRED") from exc

    if not authenticated:
        raise CollectionError("AUTH_EXPIRED")

    _save_storage_state(context, settings.storage_state_path)


@contextmanager
def authenticated_page(settings):
    _require_posix_runtime()
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
