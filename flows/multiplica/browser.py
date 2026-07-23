from contextlib import contextmanager

from playwright.sync_api import sync_playwright

from .loga import CollectionError


@contextmanager
def authenticated_page(settings):
    if not settings.storage_state_path.is_file():
        raise CollectionError("AUTH_EXPIRED")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chrome")
        context = browser.new_context(
            storage_state=str(settings.storage_state_path)
        )
        try:
            yield context.new_page()
        finally:
            context.close()
            browser.close()
