from pathlib import Path
import unittest

from flows.multiplica import loga
from flows.multiplica.loga import EXPECTED_INDICATORS, parse_indicators_html


FIXTURE = (
    Path(__file__).parent / "fixtures" / "multiplica_indicadores.html"
).read_text(encoding="utf-8")


class LogaContractTests(unittest.TestCase):
    def test_accepts_real_authenticated_page_contract(self):
        class PasswordLocator:
            def count(self):
                return 0

        class Page:
            def title(self):
                return "Indicadores SLA e Qualidade"

            def locator(self, selector):
                self.selector = selector
                return PasswordLocator()

        page = Page()
        try:
            authenticated = loga._is_authenticated_indicators_page(page)
        except AttributeError as exc:
            self.fail(f"contrato real de autenticação ausente: {exc}")

        self.assertTrue(authenticated)
        self.assertEqual(page.selector, 'input[type="password"]')

    def test_uses_real_filter_control_ids(self):
        class Page:
            def locator(self, selector):
                return selector

        try:
            controls, start_control, end_control = loga._filter_controls(Page())
        except AttributeError as exc:
            self.fail(f"seletores reais dos filtros ausentes: {exc}")

        self.assertEqual(
            controls,
            {
                "sistema": "#sistema",
                "executor": "#executor",
                "modo_calculo": "#modelo",
            },
        )
        self.assertEqual(start_control, "#dti")
        self.assertEqual(end_control, "#dtf")

    def test_accepts_official_indicator_order_and_special_rows(self):
        summary = parse_indicators_html(FIXTURE)
        lines = summary.splitlines()
        self.assertEqual(
            lines[0].split("\t"),
            ["Cidade", "SLA", "Qualidade", "Consolidado"],
        )
        self.assertEqual(tuple(lines[1].split("\t")), EXPECTED_INDICATORS)
        self.assertTrue(
            {"Total", "META", "PESO", "PESO ATINGIDO"}.issubset(
                {line.split("\t", 1)[0] for line in summary.splitlines()}
            )
        )
        peso = next(
            line for line in lines if line.split("\t", 1)[0] == "PESO"
        )
        self.assertEqual(len(peso.split("\t")), 18)
        self.assertTrue(peso.endswith("\t"))

    def test_rejects_missing_meta(self):
        html = FIXTURE.replace("<td>META</td>", "<td>ALVO</td>")
        with self.assertRaisesRegex(ValueError, "TABLE_CONTRACT_INVALID"):
            parse_indicators_html(html)

    def test_rejects_fifteen_indicators(self):
        html = FIXTURE.replace("<th>ICC</th>", "")
        with self.assertRaisesRegex(ValueError, "TABLE_CONTRACT_INVALID"):
            parse_indicators_html(html)
