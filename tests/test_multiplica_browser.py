from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from flows.multiplica.browser import _ensure_authenticated
from flows.multiplica.config import Settings
from flows.multiplica.loga import CollectionError


class _Locator:
    def __init__(self, page, name):
        self.page = page
        self.name = name

    def count(self):
        if self.name == "password-input":
            return self.page.password_inputs
        return 1

    def fill(self, value):
        self.page.fills.append((self.name, value))

    def click(self):
        self.page.login_clicks += 1
        if self.page.authenticates:
            self.page.current_title = "Indicadores SLA e Qualidade"
            self.page.password_inputs = 0

    def wait_for(self, **_kwargs):
        return None


class _Page:
    def __init__(self, *, authenticated=False, authenticates=True):
        self.current_title = (
            "Indicadores SLA e Qualidade"
            if authenticated
            else "Dashboard - Loga Internet"
        )
        self.password_inputs = 0 if authenticated else 1
        self.authenticates = authenticates
        self.fills = []
        self.login_clicks = 0
        self.goto_calls = []

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    def title(self):
        return self.current_title

    def locator(self, selector):
        if selector == 'input[type="password"]':
            return _Locator(self, "password-input")
        return _Locator(self, selector)

    def get_by_label(self, name, **_kwargs):
        return _Locator(self, name)

    def get_by_role(self, _role, *, name, **_kwargs):
        return _Locator(self, name)

    def wait_for_load_state(self, *_args, **_kwargs):
        return None


class _Context:
    def __init__(self):
        self.saved_paths = []

    def storage_state(self, *, path):
        target = Path(path)
        target.write_text("{}", encoding="utf-8")
        self.saved_paths.append(target)


class MultiplicaBrowserAuthTests(unittest.TestCase):
    def _settings(self, runtime_root):
        return Settings.from_mapping(
            {
                "MULTIPLICA_LOGA_URL": (
                    "https://dashboard.loga.net.br/indicadores"
                ),
                "MULTIPLICA_RUNTIME_ROOT": str(runtime_root),
                "MULTIPLICA_SCHEDULE_ENABLED": "false",
            }
        )

    def test_valid_session_does_not_fill_credentials(self):
        with TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir) / "multiplica")
            page = _Page(authenticated=True)

            _ensure_authenticated(page, _Context(), settings, {})

            self.assertEqual(page.fills, [])
            self.assertEqual(page.login_clicks, 0)

    def test_expired_session_logs_in_once_and_saves_state_atomically(self):
        with TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir) / "multiplica")
            page = _Page()
            context = _Context()

            _ensure_authenticated(
                page,
                context,
                settings,
                {
                    "MULTIPLICA_LOGA_USER": "gestor@example.invalid",
                    "MULTIPLICA_LOGA_PASSWORD": "segredo-de-teste",
                },
            )

            self.assertEqual(
                page.fills,
                [
                    ("E-Mail", "gestor@example.invalid"),
                    ("Senha", "segredo-de-teste"),
                ],
            )
            self.assertEqual(page.login_clicks, 1)
            self.assertEqual(
                context.saved_paths,
                [
                    settings.runtime_root
                    / "runtime"
                    / "loga-storage-state.json.tmp"
                ],
            )
            self.assertTrue(settings.storage_state_path.is_file())
            self.assertFalse(context.saved_paths[0].exists())

    def test_missing_credentials_preserves_auth_expired(self):
        with TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir) / "multiplica")

            with self.assertRaisesRegex(CollectionError, "AUTH_EXPIRED"):
                _ensure_authenticated(_Page(), _Context(), settings, {})

    def test_rejected_credentials_preserves_auth_expired(self):
        with TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir) / "multiplica")

            with self.assertRaisesRegex(CollectionError, "AUTH_EXPIRED"):
                _ensure_authenticated(
                    _Page(authenticates=False),
                    _Context(),
                    settings,
                    {
                        "MULTIPLICA_LOGA_USER": "gestor@example.invalid",
                        "MULTIPLICA_LOGA_PASSWORD": "senha-invalida",
                    },
                )


if __name__ == "__main__":
    unittest.main()
