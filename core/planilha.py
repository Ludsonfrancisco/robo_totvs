"""Leitura e validação da planilha XLSX de Transferência Múltipla (PRD §6.7.1)."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import Any

import openpyxl
from pydantic import ValidationError

from core.schema import LinhaTransferencia, PlanilhaCarregada


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


def _calcular_sha256(path: Path) -> str:
    """Calcula o SHA-256 do arquivo bruto."""
    sha256_hash = hashlib.sha256()
    with open(path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def carregar_transferencias(path: Path) -> PlanilhaCarregada:
    """Carrega e valida a planilha de Transferência Múltipla.

    Levanta :class:`PlanilhaInvalidaError` se o arquivo não existir, não for
    um XLSX válido (mesma checagem de F3 — `zipfile.is_zipfile`), ou se a
    estrutura/dados forem inválidos.
    """
    if not path.exists():
        raise PlanilhaInvalidaError(f"arquivo não encontrado: {path}")

    if not zipfile.is_zipfile(path):
        raise PlanilhaInvalidaError(
            f"arquivo não é um XLSX válido (zipfile.is_zipfile=False): {path}"
        )

    try:
        # data_only=True para ler valores calculados; read_only=True para performance
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

    # Mapeamento de headers normalizados para índices das colunas
    col_map = {
        _normalize_header_name(h): i 
        for i, h in enumerate(raw_header) 
        if h is not None
    }

    # Colunas obrigatórias conforme PRD §6.7.1
    # Nota: prod_destino e numero_serie podem ser auto-preenchidos pelo sistema
    obrigatorias = [
        "prodorig", "armazemorig", "quantidade"
    ]
    faltantes = [h for h in obrigatorias if h not in col_map]
    if faltantes:
        wb.close()
        raise PlanilhaInvalidaError(
            f"colunas obrigatórias faltantes: {', '.join(faltantes)}"
        )

    # Mapeamento reverso para facilitar criação do dicionário da linha
    # Queremos os nomes dos campos do Pydantic (que já estão normalizados na prática)
    pydantic_fields = LinhaTransferencia.model_fields.keys()
    field_to_idx = {}
    for field in pydantic_fields:
        # Tenta encontrar o campo no cabeçalho usando normalização
        norm_field = _normalize_header_name(field)
        if norm_field in col_map:
            field_to_idx[field] = col_map[norm_field]

    linhas: list[LinhaTransferencia] = []
    
    # Processa linhas de dados
    for row_idx, row in enumerate(rows_iter, start=2):  # 1-indexed, cabeçalho é linha 1
        # Pula linhas completamente vazias
        if not any(v is not None for v in row):
            continue
            
        data: dict[str, Any] = {}
        for field, col_idx in field_to_idx.items():
            if col_idx < len(row):
                data[field] = row[col_idx]
        
        try:
            linha_obj = LinhaTransferencia(**data)
            linhas.append(linha_obj)
        except ValidationError as e:
            # Extrai o primeiro erro para reportar de forma amigável
            err = e.errors()[0]
            col_name = err["loc"][0]
            msg = err["msg"]
            wb.close()
            raise PlanilhaInvalidaError(
                f"erro na linha {row_idx}, coluna '{col_name}': {msg}"
            )

    wb.close()

    if not linhas:
        raise PlanilhaInvalidaError(f"planilha não contém linhas de dados: {path}")

    return PlanilhaCarregada(
        linhas=linhas, 
        sha256=_calcular_sha256(path), 
        caminho=path
    )
