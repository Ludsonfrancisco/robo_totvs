from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, is_zipfile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException


SHEET_NAME = "Base Medição de Pagamento"
REQUIRED_HEADERS = (
    "Numero",
    "DataAbertura",
    "Protocolo",
    "Cliente",
    "Nome",
    "Cidade",
    "Bairro",
    "FimUsuario",
    "Executor",
    "FimData",
    "RazaoSocial",
    "NomeFantasia",
    "Fluxo",
    "Fluxo1",
    "Topico",
    "Causa",
    "TipoEncerramento",
    "Expurgado",
    "Motivo_Expurgo",
    "Valor Atividade",
)
MAX_WORKBOOK_BYTES = 256 * 1024 * 1024
_FIM_DATA_INDEX = REQUIRED_HEADERS.index("FimData")


class WorkbookInvalid(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkbookInfo:
    row_count: int
    headers: tuple[str, ...]
    size: int


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for pattern in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                return datetime.strptime(value, pattern).date()
            except ValueError:
                continue
    return None


def validate_workbook(path, query_start, query_end):
    start = _as_date(query_start)
    end = _as_date(query_end)
    if start is None or end is None or start > end:
        raise WorkbookInvalid("Período de consulta inválido.")

    workbook_path = Path(path)
    try:
        size = workbook_path.stat().st_size
    except OSError as error:
        raise WorkbookInvalid("Arquivo de medição inválido.") from error

    if not workbook_path.is_file() or not 0 < size <= MAX_WORKBOOK_BYTES or not is_zipfile(workbook_path):
        raise WorkbookInvalid("Arquivo de medição inválido.")

    workbook = None
    try:
        workbook = load_workbook(workbook_path, read_only=True, data_only=True)
        if SHEET_NAME not in workbook.sheetnames:
            raise WorkbookInvalid("Planilha de medição inválida.")

        worksheet = workbook[SHEET_NAME]
        rows = worksheet.iter_rows(values_only=True)
        try:
            headers = next(rows)
        except StopIteration as error:
            raise WorkbookInvalid("Planilha de medição inválida.") from error

        if headers is None or tuple(headers) != REQUIRED_HEADERS:
            raise WorkbookInvalid("Cabeçalhos da planilha inválidos.")

        row_count = 0
        for row_number, row in enumerate(rows, start=2):
            if not row or all(value is None for value in row):
                continue
            if len(row) < len(REQUIRED_HEADERS):
                raise WorkbookInvalid("Linha da planilha inválida.")

            fim_data = _as_date(row[_FIM_DATA_INDEX])
            if fim_data is None:
                raise WorkbookInvalid("Data FimData inválida.")
            if not start <= fim_data <= end:
                raise WorkbookInvalid("Data FimData fora do período de consulta.")
            row_count += 1

        if row_count == 0:
            raise WorkbookInvalid("Planilha de medição sem dados.")

        return WorkbookInfo(row_count=row_count, headers=REQUIRED_HEADERS, size=size)
    except WorkbookInvalid:
        raise
    except (BadZipFile, InvalidFileException, OSError, ValueError, KeyError, ParseError) as error:
        raise WorkbookInvalid("Planilha de medição inválida.") from error
    finally:
        if workbook is not None:
            workbook.close()
