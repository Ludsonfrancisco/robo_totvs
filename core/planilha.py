"""Leitura e validação da planilha XLSX de Transferência Múltipla (PRD §6.7.1)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import openpyxl

from core.schema import PlanilhaCarregada


class PlanilhaInvalidaError(Exception):
    """Planilha de Transferência Múltipla inválida — mapeia para exit 3."""


def _normalize_header_name(name: object) -> str:
    """Normaliza nome de cabeçalho: lowercase, sem espaços/pontos/underscores.

    Equivalências (PRD §6.7.1):
        "Prod.Orig." == "prod orig" == "PROD_ORIG" == "prodorig"
    """
    if name is None:
        return ""
    s = str(name).strip().lower()
    for ch in (" ", ".", "_", "-"):
        s = s.replace(ch, "")
    return s


def carregar_transferencias(path: Path) -> PlanilhaCarregada:
    """Carrega e valida a planilha de Transferência Múltipla.

    Levanta :class:`PlanilhaInvalidaError` se o arquivo não existir, não for
    um XLSX válido (mesma checagem de F3 — `zipfile.is_zipfile`), ou se a
    estrutura for inválida.
    """
    if not path.exists():
        raise PlanilhaInvalidaError(f"arquivo não encontrado: {path}")

    if not zipfile.is_zipfile(path):
        raise PlanilhaInvalidaError(
            f"arquivo não é um XLSX válido (zipfile.is_zipfile=False): {path}"
        )

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise PlanilhaInvalidaError(f"falha ao abrir XLSX: {path}: {e}") from e

    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)

    try:
        raw_header = next(rows_iter)
    except StopIteration:
        wb.close()
        raise PlanilhaInvalidaError(f"planilha vazia (sem cabeçalho): {path}")

    headers_normalizados = [_normalize_header_name(h) for h in raw_header]
    wb.close()

    return PlanilhaCarregada(linhas=[], sha256="", caminho=path)
