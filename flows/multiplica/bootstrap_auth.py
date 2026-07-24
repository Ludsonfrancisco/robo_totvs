import os

from playwright.sync_api import sync_playwright

from .config import Settings


def _is_authenticated_indicators_page(page) -> bool:
    return (
        page.title().strip() == "Indicadores SLA e Qualidade"
        and page.locator('input[type="password"]').count() == 0
    )


def _find_authenticated_page(context):
    for page in reversed(context.pages):
        if _is_authenticated_indicators_page(page):
            return page
    return None


def main():
    settings = Settings.from_mapping(os.environ)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context()
        page = context.new_page()
        page.goto(settings.loga_url)
        input("Conclua o login da Loga e pressione Enter para validar: ")
        if _find_authenticated_page(context) is None:
            context.close()
            browser.close()
            raise RuntimeError("AUTH_MARKER_NOT_FOUND")
        context.storage_state(path=str(settings.storage_state_path))
        context.close()
        browser.close()
    print("Sessão Multiplica salva com sucesso.")


if __name__ == "__main__":
    main()
