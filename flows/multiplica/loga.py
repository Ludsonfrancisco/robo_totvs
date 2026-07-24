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
        if tag == "table" and not self.in_table and not self.rows:
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


def _canonical_indicator_row(values: list[str]) -> list[str] | None:
    if tuple(value.casefold() for value in values) != tuple(
        value.casefold() for value in EXPECTED_INDICATORS
    ):
        return None
    return list(EXPECTED_INDICATORS)


def _validate_rows(rows: list[list[str]]) -> str:
    if not rows or not rows[0] or rows[0][0].casefold() != "cidade":
        raise ValueError("TABLE_CONTRACT_INVALID")

    simple_header = _canonical_indicator_row(rows[0][1:])
    if simple_header:
        normalized_rows = [["Cidade", *simple_header], *rows[1:]]
        data_rows = rows[1:]
        expected_columns = len(EXPECTED_INDICATORS) + 1
    elif len(rows) > 1:
        grouped_header = tuple(cell.casefold() for cell in rows[0])
        indicator_header = _canonical_indicator_row(rows[1])
        if grouped_header != (
            "cidade",
            "sla",
            "qualidade",
            "consolidado",
        ) or not indicator_header:
            raise ValueError("TABLE_CONTRACT_INVALID")
        expected_columns = len(EXPECTED_INDICATORS) + 2
        data_rows = []
        for row in rows[2:]:
            if (
                row
                and row[0] in {"PESO", "PESO ATINGIDO"}
                and len(row) == expected_columns - 1
            ):
                row = [*row, ""]
            data_rows.append(row)
        normalized_rows = [
            ["Cidade", "SLA", "Qualidade", "Consolidado"],
            indicator_header,
            *data_rows,
        ]
    else:
        raise ValueError("TABLE_CONTRACT_INVALID")

    labels = {row[0] for row in data_rows if row}
    if not EXPECTED_ROWS.issubset(labels):
        raise ValueError("TABLE_CONTRACT_INVALID")
    if any(len(row) != expected_columns for row in data_rows):
        raise ValueError("TABLE_CONTRACT_INVALID")
    return "\n".join("\t".join(row) for row in normalized_rows) + "\n"


def parse_indicators_html(html: str) -> str:
    parser = _TableParser()
    parser.feed(html)
    return _validate_rows(parser.rows)


def _selected_text(control) -> str:
    return control.locator("option:checked").inner_text().strip()


def _is_authenticated_indicators_page(page) -> bool:
    return (
        page.title().strip() == "Indicadores SLA e Qualidade"
        and page.locator('input[type="password"]').count() == 0
    )


def _filter_controls(page):
    return (
        {
            "sistema": page.locator("#sistema"),
            "executor": page.locator("#executor"),
            "modo_calculo": page.locator("#modelo"),
        },
        page.locator("#dti"),
        page.locator("#dtf"),
    )


def _summary_from_page(page) -> str:
    rows = []
    table_rows = page.locator("table tr")
    for index in range(table_rows.count()):
        rows.append(
            [
                value.strip()
                for value in table_rows.nth(index).locator("th, td").all_inner_texts()
            ]
        )
    return _validate_rows(rows)


def _wait_for_export_button(page):
    export_button = page.get_by_role(
        "button",
        name=re.compile("Excel|Baixar|Exportar", re.I),
    )
    export_button.wait_for(state="visible", timeout=60_000)
    return export_button


def collect_window(page, window: CycleWindow, settings) -> Path:
    page.goto(settings.loga_url, wait_until="networkidle")
    if not _is_authenticated_indicators_page(page):
        raise CollectionError("AUTH_EXPIRED")

    page.get_by_role("button", name="Filtros", exact=True).click()
    controls, start_control, end_control = _filter_controls(page)
    for key, expected in FILTERS.items():
        controls[key].select_option(label=expected)
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
    page.locator("table tr").nth(1).wait_for()
    export_button = _wait_for_export_button(page)
    summary = _summary_from_page(page)

    with page.expect_download(timeout=60_000) as download_info:
        export_button.click()
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
