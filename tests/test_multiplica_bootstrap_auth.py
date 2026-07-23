import os
import tempfile
import unittest
from unittest.mock import patch

from flows.multiplica import bootstrap_auth


class _Locator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class _Page:
    url = "https://dashboard.loga.net.br/login"

    def goto(self, _url):
        return None

    def title(self):
        return "Indicadores SLA e Qualidade"

    def locator(self, selector):
        if selector == 'input[type="password"]':
            return _Locator(0)
        if selector == '[data-testid="indicadores-page"]':
            return _Locator(0)
        return _Locator(0)


class _Context:
    def __init__(self):
        self.page = _Page()
        self.saved_path = None

    def new_page(self):
        return self.page

    def storage_state(self, path):
        self.saved_path = path

    def close(self):
        return None


class _Browser:
    def __init__(self, context):
        self.context = context

    def new_context(self):
        return self.context

    def close(self):
        return None


class _Chromium:
    def __init__(self, browser):
        self.browser = browser

    def launch(self, **_kwargs):
        return self.browser


class _Playwright:
    def __init__(self, browser):
        self.chromium = _Chromium(browser)


class _PlaywrightManager:
    def __init__(self, browser):
        self.playwright = _Playwright(browser)

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return None


class MultiplicaBootstrapAuthTests(unittest.TestCase):
    def test_accepts_real_authenticated_indicators_page(self):
        context = _Context()
        browser = _Browser(context)

        with tempfile.TemporaryDirectory() as runtime_root, patch.dict(
            os.environ,
            {
                "MULTIPLICA_LOGA_URL": "https://dashboard.loga.net.br/indicadores",
                "MULTIPLICA_RUNTIME_ROOT": f"{runtime_root}/multiplica",
            },
            clear=False,
        ), patch.object(
            bootstrap_auth,
            "sync_playwright",
            return_value=_PlaywrightManager(browser),
        ), patch(
            "builtins.input",
            return_value="",
        ):
            try:
                bootstrap_auth.main()
            except RuntimeError as exc:
                self.fail(f"página autenticada real foi rejeitada: {exc}")

        self.assertIsNotNone(context.saved_path)
        self.assertTrue(context.saved_path.endswith("loga-storage-state.json"))


if __name__ == "__main__":
    unittest.main()
