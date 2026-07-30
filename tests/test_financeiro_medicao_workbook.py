from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook

from flows.financeiro_medicao.workbook import (
    REQUIRED_HEADERS,
    SHEET_NAME,
    WorkbookInvalid,
    validate_workbook,
)


CANONICAL_ROW = (
    "123456",
    "11/07/2026 08:00:00",
    "PROTO-123",
    "Cliente Exemplo",
    "Maria da Silva",
    "Sao Paulo",
    "Centro",
    "Usuario Final",
    "Executor Exemplo",
    "15/07/2026 12:30:00",
    "Empresa Exemplo LTDA",
    "Empresa Exemplo",
    "Financeiro",
    "Medicao",
    "Pagamento",
    "Sem causa",
    "Encerrado",
    "Nao",
    "",
    125.50,
)


class FinanceiroMedicaoWorkbookTests(unittest.TestCase):
    def make_workbook(self, directory, *, sheet_name=SHEET_NAME, headers=REQUIRED_HEADERS, rows=()):
        path = Path(directory) / "medicao.xlsx"
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = sheet_name
        worksheet.append(list(headers))
        for row in rows:
            worksheet.append(list(row))
        workbook.save(path)
        workbook.close()
        return path

    def test_accepts_canonical_workbook_with_one_row_and_headers(self):
        with TemporaryDirectory() as directory:
            path = self.make_workbook(directory, rows=(CANONICAL_ROW,))

            info = validate_workbook(path, date(2026, 7, 11), date(2026, 7, 31))

        self.assertEqual(info.row_count, 1)
        self.assertEqual(info.headers, REQUIRED_HEADERS)
        self.assertGreater(info.size, 0)

    def test_rejects_wrong_sheet_name(self):
        with TemporaryDirectory() as directory:
            path = self.make_workbook(directory, sheet_name="Outra aba", rows=(CANONICAL_ROW,))

            with self.assertRaises(WorkbookInvalid):
                validate_workbook(path, date(2026, 7, 11), date(2026, 7, 31))

    def test_rejects_fim_data_outside_period(self):
        row = list(CANONICAL_ROW)
        row[9] = "01/08/2026 00:00:00"
        with TemporaryDirectory() as directory:
            path = self.make_workbook(directory, rows=(row,))

            with self.assertRaisesRegex(WorkbookInvalid, "per[ií]odo"):
                validate_workbook(path, date(2026, 7, 11), date(2026, 7, 31))

    def test_rejects_wrong_headers(self):
        headers = list(REQUIRED_HEADERS)
        headers[0] = "Número"
        with TemporaryDirectory() as directory:
            path = self.make_workbook(directory, headers=headers, rows=(CANONICAL_ROW,))

            with self.assertRaises(WorkbookInvalid):
                validate_workbook(path, date(2026, 7, 11), date(2026, 7, 31))

    def test_rejects_workbook_without_data_rows(self):
        with TemporaryDirectory() as directory:
            path = self.make_workbook(directory)

            with self.assertRaises(WorkbookInvalid):
                validate_workbook(path, date(2026, 7, 11), date(2026, 7, 31))

    def test_rejects_missing_and_non_xlsx_files(self):
        with TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing.xlsx"
            text_path = Path(directory) / "downloaded.xlsx"
            text_path.write_text("not an xlsx", encoding="utf-8")

            for path in (missing_path, text_path):
                with self.subTest(path=path.name):
                    with self.assertRaises(WorkbookInvalid):
                        validate_workbook(path, date(2026, 7, 11), date(2026, 7, 31))

    def test_rejects_xlsx_with_malformed_xml(self):
        with TemporaryDirectory() as directory:
            valid_path = self.make_workbook(directory, rows=(CANONICAL_ROW,))
            malformed_path = Path(directory) / "malformed.xlsx"
            with ZipFile(valid_path) as source, ZipFile(malformed_path, "w", ZIP_DEFLATED) as target:
                for entry in source.infolist():
                    content = b"<workbook>" if entry.filename == "xl/workbook.xml" else source.read(entry.filename)
                    target.writestr(entry, content)

            with self.assertRaises(WorkbookInvalid):
                validate_workbook(malformed_path, date(2026, 7, 11), date(2026, 7, 31))

    def test_rejects_inverted_query_period(self):
        with TemporaryDirectory() as directory:
            path = self.make_workbook(directory, rows=(CANONICAL_ROW,))

            with self.assertRaises(WorkbookInvalid):
                validate_workbook(path, date(2026, 7, 31), date(2026, 7, 11))

    def test_accepts_supported_date_formats_and_inclusive_limits(self):
        start_row = list(CANONICAL_ROW)
        start_row[9] = "2026-07-11 00:00:00"
        end_row = list(CANONICAL_ROW)
        end_row[9] = datetime(2026, 7, 31, 23, 59, 59)
        with TemporaryDirectory() as directory:
            path = self.make_workbook(directory, rows=(start_row, end_row))

            info = validate_workbook(path, date(2026, 7, 11), date(2026, 7, 31))

        self.assertEqual(info.row_count, 2)


if __name__ == "__main__":
    unittest.main()
