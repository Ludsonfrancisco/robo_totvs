from pathlib import Path
import unittest

from flows.multiplica.loga import EXPECTED_INDICATORS, parse_indicators_html


FIXTURE = (
    Path(__file__).parent / "fixtures" / "multiplica_indicadores.html"
).read_text(encoding="utf-8")


class LogaContractTests(unittest.TestCase):
    def test_accepts_official_indicator_order_and_special_rows(self):
        summary = parse_indicators_html(FIXTURE)
        header = summary.splitlines()[0].split("\t")
        self.assertEqual(tuple(header[1:]), EXPECTED_INDICATORS)
        self.assertTrue(
            {"Total", "META", "PESO", "PESO ATINGIDO"}.issubset(
                {line.split("\t", 1)[0] for line in summary.splitlines()}
            )
        )

    def test_rejects_missing_meta(self):
        html = FIXTURE.replace("<td>META</td>", "<td>ALVO</td>")
        with self.assertRaisesRegex(ValueError, "TABLE_CONTRACT_INVALID"):
            parse_indicators_html(html)

    def test_rejects_fifteen_indicators(self):
        html = FIXTURE.replace("<th>ICC</th>", "")
        with self.assertRaisesRegex(ValueError, "TABLE_CONTRACT_INVALID"):
            parse_indicators_html(html)
