from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from flows.financeiro_medicao.cycles import window_for
from flows.financeiro_medicao.loga import CollectionError, apply_period, collect


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "financeiro_medicao_page.html"
)


class _ContractParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = {}
        self.options = {}
        self.buttons = []
        self._select = None
        self._button = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"select", "input"}:
            control_id = attributes.get("id")
            self.controls[control_id] = attributes
            if tag == "select":
                self._select = control_id
                self.options[control_id] = []
        elif tag == "option" and self._select:
            self.options[self._select].append(attributes.get("value"))
        elif tag == "button":
            self._button = attributes
            self._text = []

    def handle_data(self, data):
        if self._button is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "button":
            self.buttons.append(
                (
                    " ".join("".join(self._text).split()),
                    self._button,
                )
            )
            self._button = None


class _Locator:
    def __init__(self, page, name, *, visible=True):
        self.page = page
        self.name = name
        self.value = ""
        self.visible = visible
        self.wait_calls = []

    def count(self):
        if self.name == 'input[type="password"]':
            return self.page.password_inputs
        if self.name == "Calculando medição...":
            return int(self.page.has_dialog)
        return 1

    def is_visible(self):
        return self.visible

    def fill(self, value):
        self.value = value

    def select_option(self, *, value):
        self.value = value

    def input_value(self):
        if self.page.mismatched_control == self.name:
            return "valor-incorreto"
        return self.value

    def wait_for(self, **kwargs):
        self.wait_calls.append(kwargs)

    def click(self):
        self.page.clicks.append(self.name)
        if self.name == "Filtros":
            self.page.controls["#dti"].visible = True


class _Download:
    def __init__(
        self,
        payload=b"workbook",
        *,
        fails=False,
        omit=False,
        competing_destination=None,
    ):
        self.payload = payload
        self.fails = fails
        self.omit = omit
        self.competing_destination = competing_destination
        self.saved_to = None

    def save_as(self, destination):
        self.saved_to = Path(destination)
        if self.fails:
            raise RuntimeError("falha simulada sem dados sensíveis")
        if not self.omit:
            self.saved_to.write_bytes(self.payload)
        if self.competing_destination is not None:
            self.competing_destination.write_bytes(b"concorrente")


class _DownloadInfo:
    def __init__(self, download, *, timeout=False):
        self.value = download
        self.timeout = timeout

    def __enter__(self):
        if self.timeout:
            raise PlaywrightTimeoutError("timeout simulado")
        return self

    def __exit__(self, *_args):
        return False


class _Page:
    def __init__(
        self,
        *,
        title="Medição de Pagamento à Terceiros",
        password_inputs=0,
        dates_visible=False,
        mismatched_control=None,
        navigation_timeout=False,
        download=None,
        download_timeout=False,
        has_dialog=True,
    ):
        self.current_title = title
        self.password_inputs = password_inputs
        self.mismatched_control = mismatched_control
        self.navigation_timeout = navigation_timeout
        self.download = download or _Download()
        self.download_timeout = download_timeout
        self.has_dialog = has_dialog
        self.goto_calls = []
        self.clicks = []
        self.role_calls = []
        self.expect_download_calls = []
        self.controls = {
            "#modoCalculo": _Locator(self, "#modoCalculo"),
            "#tipoMedicao": _Locator(self, "#tipoMedicao"),
            "#tipoAgrupamento": _Locator(self, "#tipoAgrupamento"),
            "#dti": _Locator(self, "#dti", visible=dates_visible),
            "#dtf": _Locator(self, "#dtf", visible=dates_visible),
            'input[type="password"]': _Locator(
                self, 'input[type="password"]'
            ),
            "Calculando medição...": _Locator(
                self, "Calculando medição..."
            ),
        }
        self.buttons = {
            "Filtros": _Locator(self, "Filtros"),
            "Pesquisar": _Locator(self, "Pesquisar"),
            "Exportar Atendimentos": _Locator(
                self, "Exportar Atendimentos"
            ),
        }

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        if self.navigation_timeout:
            raise PlaywrightTimeoutError("timeout simulado")

    def title(self):
        return self.current_title

    def locator(self, selector):
        return self.controls[selector]

    def get_by_role(self, role, *, name, exact):
        self.role_calls.append((role, name, exact))
        return self.buttons[name]

    def get_by_text(self, text, *, exact):
        self.text_call = (text, exact)
        return self.controls[text]

    def expect_download(self, **kwargs):
        self.expect_download_calls.append(kwargs)
        return _DownloadInfo(
            self.download,
            timeout=self.download_timeout,
        )


class _Settings:
    loga_url = "https://dashboard.loga.net.br/medicao_pagamento"


class FinanceiroMedicaoFixtureTests(unittest.TestCase):
    def test_fixture_matches_sanitized_live_contract(self):
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        parser = _ContractParser()
        parser.feed(html)

        self.assertEqual(
            set(parser.controls),
            {
                "modoCalculo",
                "tipoMedicao",
                "tipoAgrupamento",
                "dti",
                "dtf",
            },
        )
        self.assertEqual(
            parser.options,
            {
                "modoCalculo": ["Todos", "Expurgados"],
                "tipoMedicao": ["", "Auditados", "NaoAuditados"],
                "tipoAgrupamento": ["cidade", "usuario"],
            },
        )
        self.assertEqual(
            [text for text, _attrs in parser.buttons],
            ["Filtros", "Exportar Atendimentos", "Pesquisar"],
        )
        self.assertEqual(
            [attrs.get("id") for _text, attrs in parser.buttons],
            [None, "btn", "btn"],
        )
        lowered = html.casefold()
        for forbidden in ("_token", "password", "cookie", "cliente"):
            self.assertNotIn(forbidden, lowered)


class FinanceiroMedicaoCollectionTests(unittest.TestCase):
    def test_collection_error_preserves_code(self):
        error = CollectionError("FILTER_MISMATCH")
        self.assertEqual(error.code, "FILTER_MISMATCH")
        self.assertEqual(str(error), "FILTER_MISMATCH")

    def test_apply_period_uses_iso_dates_for_current_and_final_windows(self):
        for day, expected in (
            (date(2026, 7, 30), ("2026-07-11", "2026-07-30")),
            (date(2026, 8, 11), ("2026-07-11", "2026-08-10")),
        ):
            with self.subTest(day=day):
                page = _Page()
                apply_period(page, window_for(day))
                self.assertEqual(
                    page.controls["#dti"].input_value(), expected[0]
                )
                self.assertEqual(
                    page.controls["#dtf"].input_value(), expected[1]
                )

    def test_collect_applies_exact_filters_and_downloads_destination(self):
        with TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "medicao.xlsx"
            page = _Page()

            result = collect(
                page,
                window_for(date(2026, 7, 30)),
                _Settings(),
                destination,
            )

            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), b"workbook")
            self.assertEqual(
                page.goto_calls,
                [
                    (
                        _Settings.loga_url,
                        {"wait_until": "networkidle"},
                    )
                ],
            )
            self.assertEqual(page.controls["#modoCalculo"].value, "Expurgados")
            self.assertEqual(page.controls["#tipoMedicao"].value, "")
            self.assertEqual(page.controls["#tipoAgrupamento"].value, "cidade")
            self.assertEqual(
                page.clicks,
                ["Filtros", "Pesquisar", "Exportar Atendimentos"],
            )
            self.assertEqual(
                page.role_calls,
                [
                    ("button", "Filtros", True),
                    ("button", "Pesquisar", True),
                    ("button", "Exportar Atendimentos", True),
                ],
            )
            self.assertEqual(
                page.controls["Calculando medição..."].wait_calls,
                [{"state": "hidden", "timeout": 120_000}],
            )
            self.assertEqual(
                page.buttons["Exportar Atendimentos"].wait_calls,
                [{"state": "visible", "timeout": 120_000}],
            )
            self.assertEqual(
                page.expect_download_calls,
                [{"timeout": 120_000}],
            )
            self.assertEqual(page.download.saved_to.parent, destination.parent)
            self.assertNotEqual(page.download.saved_to, destination)
            self.assertFalse(page.download.saved_to.exists())
            UUID(page.download.saved_to.name.split(".")[-2])

    def test_collect_does_not_open_filters_when_dates_are_visible(self):
        with TemporaryDirectory() as temp_dir:
            page = _Page(dates_visible=True, has_dialog=False)

            collect(
                page,
                window_for(date(2026, 7, 30)),
                _Settings(),
                Path(temp_dir) / "medicao.xlsx",
            )

        self.assertNotIn("Filtros", page.clicks)

    def test_filter_mismatch_is_rejected_before_download(self):
        with TemporaryDirectory() as temp_dir:
            page = _Page(mismatched_control="#tipoAgrupamento")

            with self.assertRaisesRegex(
                CollectionError, "FILTER_MISMATCH"
            ) as raised:
                collect(
                    page,
                    window_for(date(2026, 7, 30)),
                    _Settings(),
                    Path(temp_dir) / "medicao.xlsx",
                )

            self.assertEqual(raised.exception.code, "FILTER_MISMATCH")
            self.assertEqual(page.expect_download_calls, [])

    def test_authentication_contract_rejects_wrong_title_or_password(self):
        for page in (
            _Page(title="Dashboard - Loga Internet"),
            _Page(password_inputs=1),
        ):
            with self.subTest(title=page.current_title):
                with TemporaryDirectory() as temp_dir:
                    with self.assertRaisesRegex(
                        CollectionError, "AUTH_EXPIRED"
                    ):
                        collect(
                            page,
                            window_for(date(2026, 7, 30)),
                            _Settings(),
                            Path(temp_dir) / "medicao.xlsx",
                        )

    def test_navigation_timeout_has_specific_code(self):
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(
                CollectionError, "NAVIGATION_TIMEOUT"
            ) as raised:
                collect(
                    _Page(navigation_timeout=True),
                    window_for(date(2026, 7, 30)),
                    _Settings(),
                    Path(temp_dir) / "medicao.xlsx",
                )
        self.assertEqual(raised.exception.code, "NAVIGATION_TIMEOUT")

    def test_download_timeout_has_specific_code(self):
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(
                CollectionError, "DOWNLOAD_TIMEOUT"
            ) as raised:
                collect(
                    _Page(download_timeout=True),
                    window_for(date(2026, 7, 30)),
                    _Settings(),
                    Path(temp_dir) / "medicao.xlsx",
                )
        self.assertEqual(raised.exception.code, "DOWNLOAD_TIMEOUT")

    def test_failed_missing_or_empty_download_is_rejected(self):
        cases = (
            _Download(fails=True),
            _Download(omit=True),
            _Download(payload=b""),
        )
        for download in cases:
            with self.subTest(
                fails=download.fails,
                omit=download.omit,
                size=len(download.payload),
            ):
                with TemporaryDirectory() as temp_dir:
                    with self.assertRaisesRegex(
                        CollectionError, "DOWNLOAD_FAILED"
                    ):
                        collect(
                            _Page(download=download),
                            window_for(date(2026, 7, 30)),
                            _Settings(),
                            Path(temp_dir) / "medicao.xlsx",
                        )

    def test_existing_destination_is_not_overwritten(self):
        with TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "medicao.xlsx"
            destination.write_bytes(b"original")
            page = _Page()

            with self.assertRaisesRegex(
                CollectionError, "DOWNLOAD_FAILED"
            ):
                collect(
                    page,
                    window_for(date(2026, 7, 30)),
                    _Settings(),
                    destination,
                )

            self.assertEqual(destination.read_bytes(), b"original")
            self.assertEqual(page.goto_calls, [])

    def test_destination_created_during_download_is_preserved(self):
        with TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "medicao.xlsx"
            download = _Download(competing_destination=destination)
            page = _Page(download=download)

            with self.assertRaisesRegex(
                CollectionError, "DOWNLOAD_FAILED"
            ):
                collect(
                    page,
                    window_for(date(2026, 7, 30)),
                    _Settings(),
                    destination,
                )

            self.assertEqual(destination.read_bytes(), b"concorrente")
            self.assertNotEqual(download.saved_to, destination)
            self.assertFalse(download.saved_to.exists())
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_unsupported_atomic_link_fails_and_cleans_only_temp(self):
        with TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "medicao.xlsx"
            download = _Download()

            with patch(
                "os.link",
                side_effect=OSError("hard link indisponível"),
            ):
                with self.assertRaisesRegex(
                    CollectionError, "DOWNLOAD_FAILED"
                ):
                    collect(
                        _Page(download=download),
                        window_for(date(2026, 7, 30)),
                        _Settings(),
                        destination,
                    )

            self.assertFalse(destination.exists())
            self.assertIsNotNone(download.saved_to)
            self.assertFalse(download.saved_to.exists())
            self.assertEqual(list(destination.parent.iterdir()), [])

    def test_missing_destination_parent_is_rejected(self):
        with TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "missing" / "medicao.xlsx"
            page = _Page()

            with self.assertRaisesRegex(
                CollectionError, "DOWNLOAD_FAILED"
            ):
                collect(
                    page,
                    window_for(date(2026, 7, 30)),
                    _Settings(),
                    destination,
                )

            self.assertFalse(destination.parent.exists())
            self.assertEqual(page.goto_calls, [])


if __name__ == "__main__":
    unittest.main()
