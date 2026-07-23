from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
import re

from .bundle import FILTERS, build_bundle
from .cycles import CycleWindow


EXPECTED_ROWS = {"Total", "META", "PESO", "PESO ATINGIDO"}
EXPECTED_INDICATORS = (
    "IIP", "IIPP", "IMEP", "IMEPP", "ISP", "ICP", "IRP", "IRPP",
    "IRR", "IQA", "IQIv", "IQRv", "RTV", "RST", "ICT", "ICC",
)


class CollectionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_cell = False
        self.cell_parts = []
        self.row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and attrs.get("data-testid") == "indicadores-table":
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.row = []
        elif self.in_table and tag in {"th", "td"}:
            self.in_cell = True
            self.cell_parts = []

    def handle_data(self, data):
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag):
        if self.in_table and tag in {"th", "td"}:
            self.row.append(" ".join("".join(self.cell_parts).split()))
            self.in_cell = False
        elif self.in_table and tag == "tr" and self.row:
            self.rows.append(self.row)
        elif self.in_table and tag == "table":
            self.in_table = False


def _validate_rows(rows: list[list[str]]) -> str:
    if not rows or tuple(rows[0][1:]) != EXPECTED_INDICATORS:
        raise ValueError("TABLE_CONTRACT_INVALID")
    labels = {row[0] for row in rows[1:] if row}
    if not EXPECTED_ROWS.issubset(labels):
        raise ValueError("TABLE_CONTRACT_INVALID")
    expected_columns = len(EXPECTED_INDICATORS) + 1
    if any(len(row) != expected_columns for row in rows):
        raise ValueError("TABLE_CONTRACT_INVALID")
    return "\n".join("\t".join(row) for row in rows) + "\n"


def parse_indicators_html(html: str) -> str:
    parser = _TableParser()
    parser.feed(html)
    return _validate_rows(parser.rows)


def _selected_text(control) -> str:
    return control.locator("option:checked").inner_text().strip()


def _summary_from_page(page) -> str:
    rows = []
    table_rows = page.locator('[data-testid="indicadores-table"] tr')
    for index in range(table_rows.count()):
        rows.append(
            [
                value.strip()
                for value in table_rows.nth(index).locator("th, td").all_inner_texts()
            ]
        )
    return _validate_rows(rows)


def collect_window(page, window: CycleWindow, settings) -> Path:
    page.goto(settings.loga_url, wait_until="domcontentloaded")
    if page.locator('[data-testid="indicadores-page"]').count() != 1:
        raise CollectionError("AUTH_EXPIRED")

    controls = {
        "sistema": page.get_by_label(re.compile("^Sistema$", re.I)),
        "executor": page.get_by_label(re.compile("^Executor$", re.I)),
        "modo_calculo": page.get_by_label(
            re.compile("^Modo de Cálculo$", re.I)
        ),
    }
    for key, expected in FILTERS.items():
        controls[key].select_option(label=expected)
    start_control = page.get_by_label(re.compile("^Data Início$", re.I))
    end_control = page.get_by_label(re.compile("^Data Fim$", re.I))
    start_control.fill(window.query_start.isoformat())
    end_control.fill(window.query_end.isoformat())
    page.get_by_role(
        "button", name=re.compile("Pesquisar|Consultar", re.I)
    ).click()

    if any(_selected_text(controls[key]) != value for key, value in FILTERS.items()):
        raise CollectionError("FILTER_MISMATCH")
    if (
        start_control.input_value() != window.query_start.isoformat()
        or end_control.input_value() != window.query_end.isoformat()
    ):
        raise CollectionError("FILTER_MISMATCH")
    summary = _summary_from_page(page)

    with page.expect_download() as download_info:
        page.get_by_role(
            "button", name=re.compile("Excel|Baixar|Exportar", re.I)
        ).click()
    download = download_info.value
    download_path = download.path()
    if not download_path:
        raise CollectionError("DOWNLOAD_FAILED")
    workbook = Path(download_path).read_bytes()
    return build_bundle(
        runtime_root=settings.runtime_root,
        window=window,
        summary_text=summary,
        workbook_bytes=workbook,
        captured_at=datetime.now().astimezone(),
    )
