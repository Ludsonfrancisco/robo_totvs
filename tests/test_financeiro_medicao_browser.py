from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch
from uuid import UUID

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from flows.financeiro_medicao.browser import (
    _ensure_authenticated,
    _is_authenticated,
    _save_storage_state,
    authenticated_page,
)
from flows.financeiro_medicao.config import Settings
from flows.financeiro_medicao.loga import CollectionError


AUTH_TITLE = "Medição de Pagamento à Terceiros"


class _Locator:
    def __init__(self, page, name):
        self.page = page
        self.name = name

    def count(self):
        if self.name == "password":
            return self.page.password_inputs
        if self.name in {"E-Mail", "Senha", "Entrar"}:
            return self.page.login_controls
        return 1

    def fill(self, value):
        self.page.fills.append((self.name, value))

    def click(self):
        self.page.login_clicks += 1
        if self.page.login_timeout:
            raise PlaywrightTimeoutError("timeout simulado")
        if self.page.accepts_credentials:
            self.page.logged_in = True
            self.page.password_inputs = 0

    def wait_for(self, **kwargs):
        self.page.wait_calls.append((self.name, kwargs))
        if self.page.wait_timeout:
            raise PlaywrightTimeoutError("timeout simulado")


class _Page:
    def __init__(
        self,
        *,
        authenticated=False,
        accepts_credentials=True,
        login_controls=1,
        login_timeout=False,
        wait_timeout=False,
    ):
        self.logged_in = authenticated
        self.accepts_credentials = accepts_credentials
        self.login_controls = login_controls
        self.login_timeout = login_timeout
        self.wait_timeout = wait_timeout
        self.current_title = AUTH_TITLE if authenticated else "Login"
        self.url = (
            "https://dashboard.loga.net.br/medicao_pagamento"
            if authenticated
            else "https://dashboard.loga.net.br/login"
        )
        self.password_inputs = 0 if authenticated else 1
        self.fills = []
        self.login_clicks = 0
        self.goto_calls = []
        self.wait_calls = []

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        if self.logged_in:
            self.url = url
            self.current_title = AUTH_TITLE
            self.password_inputs = 0
        else:
            self.url = "https://dashboard.loga.net.br/login"
            self.current_title = "Login"
            self.password_inputs = 1

    def title(self):
        return self.current_title

    def locator(self, selector):
        if selector != 'input[type="password"]':
            raise AssertionError(f"seletor inesperado: {selector}")
        return _Locator(self, "password")

    def get_by_label(self, name, *, exact):
        self.label_exact = exact
        return _Locator(self, name)

    def get_by_role(self, role, *, name, exact):
        self.role_call = (role, name, exact)
        return _Locator(self, name)

    def wait_for_load_state(self, state, **kwargs):
        self.load_state_call = (state, kwargs)
        if self.wait_timeout:
            raise PlaywrightTimeoutError("timeout simulado")


class _Context:
    def __init__(
        self,
        page=None,
        *,
        events=None,
        storage_error=False,
    ):
        self.page = page
        self.events = events
        self.storage_error = storage_error
        self.saved_paths = []
        self.closed = False

    def new_page(self):
        return self.page

    def storage_state(self, *, path):
        target = Path(path)
        if self.events is not None:
            self.events.append(("storage_state", target))
        if not target.exists():
            raise AssertionError("storage state temporário não foi pré-criado")
        target.write_text('{"cookies": []}', encoding="utf-8")
        self.saved_paths.append(target)
        if self.storage_error:
            raise RuntimeError("storage failure")

    def close(self):
        self.closed = True


class _Browser:
    def __init__(self, page, *, context_error=False):
        self.page = page
        self.context = _Context(page)
        self.context_error = context_error
        self.new_context_options = None
        self.closed = False

    def new_context(self, **kwargs):
        self.new_context_options = kwargs
        if self.context_error:
            raise RuntimeError("context failure")
        return self.context

    def close(self):
        self.closed = True


class _Chromium:
    def __init__(self, browser):
        self.browser = browser
        self.launch_options = None

    def launch(self, **kwargs):
        self.launch_options = kwargs
        return self.browser


class _PlaywrightManager:
    def __init__(self, playwright):
        self.playwright = playwright

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return False


class FinanceiroMedicaoBrowserTests(unittest.TestCase):
    def _settings(self, runtime_root, *, username="user", password="secret"):
        return Settings.from_mapping(
            {
                "FINANCEIRO_MEDICAO_LOGA_URL": (
                    "https://dashboard.loga.net.br/medicao_pagamento"
                ),
                "FINANCEIRO_MEDICAO_RUNTIME_ROOT": str(runtime_root),
                "LOGA_DASHBOARD_USER": username,
                "LOGA_DASHBOARD_PASSWORD": password,
            }
        )

    def test_authentication_requires_title_path_and_no_password(self):
        page = _Page(authenticated=True)
        self.assertTrue(_is_authenticated(page))

        page.url = "https://dashboard.loga.net.br/outra-rota"
        self.assertFalse(_is_authenticated(page))
        page.url = "https://dashboard.loga.net.br/medicao_pagamento"
        page.current_title = "Outro título"
        self.assertFalse(_is_authenticated(page))
        page.current_title = AUTH_TITLE
        page.password_inputs = 1
        self.assertFalse(_is_authenticated(page))

    def test_valid_session_does_not_fill_credentials_or_save_state(self):
        with TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir) / "financeiro_medicao")
            page = _Page(authenticated=True)
            context = _Context()

            _ensure_authenticated(page, context, settings)

            self.assertEqual(page.fills, [])
            self.assertEqual(page.login_clicks, 0)
            self.assertEqual(context.saved_paths, [])

    def test_expired_session_uses_settings_and_saves_isolated_state(self):
        with TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "financeiro_medicao"
            (runtime_root / "runtime").mkdir(parents=True)
            settings = self._settings(
                runtime_root,
                username="gestor@example.invalid",
                password="segredo-de-teste",
            )
            page = _Page()
            context = _Context()

            with patch.dict(
                os.environ,
                {
                    "MULTIPLICA_LOGA_USER": "nao-usar",
                    "MULTIPLICA_LOGA_PASSWORD": "nao-usar",
                },
            ):
                _ensure_authenticated(page, context, settings)

            self.assertEqual(
                page.fills,
                [
                    ("E-Mail", "gestor@example.invalid"),
                    ("Senha", "segredo-de-teste"),
                ],
            )
            self.assertEqual(page.role_call, ("button", "Entrar", True))
            self.assertEqual(page.login_clicks, 1)
            self.assertEqual(
                page.goto_calls,
                [
                    (settings.loga_url, {"wait_until": "networkidle"}),
                    (settings.loga_url, {"wait_until": "networkidle"}),
                ],
            )
            self.assertEqual(len(context.saved_paths), 1)
            temporary = context.saved_paths[0]
            self.assertEqual(temporary.parent, settings.storage_state_path.parent)
            self.assertNotEqual(
                temporary,
                settings.storage_state_path.with_name(
                    settings.storage_state_path.name + ".tmp"
                ),
            )
            UUID(temporary.name.split(".")[-2])
            self.assertTrue(settings.storage_state_path.is_file())
            self.assertFalse(temporary.exists())
            self.assertNotEqual(
                settings.storage_state_path,
                runtime_root.parent
                / "multiplica"
                / "runtime"
                / "loga-storage-state.json",
            )
            if os.name != "nt":
                self.assertEqual(
                    settings.storage_state_path.stat().st_mode & 0o777,
                    0o600,
                )

    def test_storage_state_temp_is_exclusive_and_restricted_before_write(self):
        with TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            destination = directory / "loga-storage-state.json"
            events = []
            context = _Context(events=events)
            real_open = os.open
            real_chmod = os.chmod

            def recording_open(path, flags, mode):
                events.append(("open", Path(path), flags, mode))
                return real_open(path, flags, mode)

            def recording_chmod(path, mode):
                events.append(("chmod", Path(path), mode))
                return real_chmod(path, mode)

            with (
                patch(
                    "flows.financeiro_medicao.browser.os.open",
                    side_effect=recording_open,
                ),
                patch(
                    "flows.financeiro_medicao.browser.os.chmod",
                    side_effect=recording_chmod,
                ),
            ):
                _save_storage_state(context, destination)

            self.assertEqual(
                [event[0] for event in events[:3]],
                ["open", "chmod", "storage_state"],
            )
            open_event = events[0]
            self.assertEqual(
                open_event[2] & (os.O_CREAT | os.O_EXCL | os.O_WRONLY),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            self.assertEqual(open_event[3], 0o600)
            temporary = context.saved_paths[0]
            UUID(temporary.name.split(".")[-2])
            self.assertFalse(temporary.exists())
            self.assertTrue(destination.is_file())

    def test_storage_state_failure_preserves_final_and_removes_uuid_temp(self):
        with TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "loga-storage-state.json"
            destination.write_bytes(b"original")
            context = _Context(storage_error=True)

            with self.assertRaisesRegex(RuntimeError, "storage"):
                _save_storage_state(context, destination)

            self.assertEqual(destination.read_bytes(), b"original")
            self.assertEqual(len(context.saved_paths), 1)
            temporary = context.saved_paths[0]
            self.assertNotEqual(temporary, destination)
            self.assertFalse(temporary.exists())
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_missing_credentials_form_or_rejection_maps_to_auth_expired(self):
        cases = (
            (self._settings, _Page(login_controls=0)),
            (
                lambda root: self._settings(root, username=""),
                _Page(),
            ),
            (
                lambda root: self._settings(root, password=""),
                _Page(),
            ),
            (self._settings, _Page(accepts_credentials=False)),
            (self._settings, _Page(login_timeout=True)),
            (self._settings, _Page(wait_timeout=True)),
        )
        for settings_factory, page in cases:
            with self.subTest(
                controls=page.login_controls,
                accepts=page.accepts_credentials,
                click_timeout=page.login_timeout,
                wait_timeout=page.wait_timeout,
            ):
                with TemporaryDirectory() as temp_dir:
                    runtime_root = Path(temp_dir) / "financeiro_medicao"
                    (runtime_root / "runtime").mkdir(parents=True)
                    settings = settings_factory(runtime_root)
                    with self.assertRaisesRegex(
                        CollectionError, "AUTH_EXPIRED"
                    ) as raised:
                        _ensure_authenticated(page, _Context(), settings)
                    self.assertEqual(raised.exception.code, "AUTH_EXPIRED")
                    self.assertFalse(settings.storage_state_path.exists())

    def test_authenticated_page_uses_chrome_and_exclusive_state_then_closes(self):
        with TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "financeiro_medicao"
            (runtime_root / "runtime").mkdir(parents=True)
            settings = self._settings(runtime_root)
            settings.storage_state_path.write_text("{}", encoding="utf-8")
            page = _Page(authenticated=True)
            browser = _Browser(page)
            chromium = _Chromium(browser)
            manager = _PlaywrightManager(
                SimpleNamespace(chromium=chromium)
            )

            with patch(
                "flows.financeiro_medicao.browser.sync_playwright",
                return_value=manager,
            ):
                with authenticated_page(settings) as yielded:
                    self.assertIs(yielded, page)

            self.assertEqual(
                chromium.launch_options,
                {"headless": True, "channel": "chrome"},
            )
            self.assertEqual(
                browser.new_context_options,
                {"storage_state": str(settings.storage_state_path)},
            )
            self.assertTrue(browser.context.closed)
            self.assertTrue(browser.closed)

    def test_authenticated_page_closes_resources_when_consumer_fails(self):
        with TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "financeiro_medicao"
            (runtime_root / "runtime").mkdir(parents=True)
            settings = self._settings(runtime_root)
            page = _Page(authenticated=True)
            browser = _Browser(page)
            manager = _PlaywrightManager(
                SimpleNamespace(chromium=_Chromium(browser))
            )

            with patch(
                "flows.financeiro_medicao.browser.sync_playwright",
                return_value=manager,
            ):
                with self.assertRaisesRegex(RuntimeError, "consumer"):
                    with authenticated_page(settings):
                        raise RuntimeError("consumer failure")

            self.assertTrue(browser.context.closed)
            self.assertTrue(browser.closed)

    def test_authenticated_page_closes_browser_when_context_creation_fails(self):
        with TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "financeiro_medicao"
            (runtime_root / "runtime").mkdir(parents=True)
            settings = self._settings(runtime_root)
            browser = _Browser(
                _Page(authenticated=True),
                context_error=True,
            )
            manager = _PlaywrightManager(
                SimpleNamespace(chromium=_Chromium(browser))
            )

            with patch(
                "flows.financeiro_medicao.browser.sync_playwright",
                return_value=manager,
            ):
                with self.assertRaisesRegex(RuntimeError, "context"):
                    with authenticated_page(settings):
                        self.fail("o contexto não deveria ser criado")

            self.assertTrue(browser.closed)


if __name__ == "__main__":
    unittest.main()
