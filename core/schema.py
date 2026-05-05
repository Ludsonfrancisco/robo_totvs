from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Tecnico(BaseModel):
    code: str
    name: Optional[str] = None
    login: Optional[str] = None
    status: Optional[str] = None
    email: Optional[str] = None


class CheckpointItem(BaseModel):
    code: str
    status: str = "pendente"  # pendente, processando, sucesso, falhou
    tentativas: int = 0
    arquivo: Optional[str] = None
    hash_sha256: Optional[str] = None
    erro_msg: Optional[str] = None


class LinhaTransferencia(BaseModel):
    """Uma linha do grid `Transferencia Mod. II - INCLUIR` (PRD §6.7.1)."""

    prod_orig: str = Field(min_length=1)
    desc_orig: Optional[str] = None
    um_orig: Optional[str] = None
    armazem_orig: str = Field(min_length=1)
    endereco_orig: Optional[str] = None
    prod_destino: str = Field(min_length=1)
    desc_destino: Optional[str] = None
    um_destino: Optional[str] = None
    armazem_destino: str = Field(min_length=1)
    endereco_destino: Optional[str] = None
    numero_serie: str = Field(min_length=1)
    lote: Optional[str] = None
    sub_lote: Optional[str] = None
    validade: Optional[date] = None
    potencia: Optional[Decimal] = None
    quantidade: Decimal
    qt_2aum: Optional[Decimal] = None
    estornado: Optional[str] = None
    sequencia: Optional[str] = None
    lote_destino: Optional[str] = None

    @field_validator("validade", mode="before")
    @classmethod
    def _parse_validade(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, date) and not isinstance(v, datetime):
            return v
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, str):
            return datetime.strptime(v.strip(), "%d/%m/%Y").date()
        raise ValueError(f"validade inválida: {v!r}")

    @field_validator("quantidade", "potencia", "qt_2aum", mode="before")
    @classmethod
    def _parse_decimal(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, Decimal):
            return v
        try:
            return Decimal(str(v).replace(",", "."))
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"valor decimal inválido: {v!r}") from e


class CheckpointTransferenciaMultipla(BaseModel):
    """Checkpoint de uma execução F7 (PRD §6.7.4)."""

    id_execucao: str
    planilha_origem: str
    planilha_sha256: str
    numero_documento: Optional[str] = None
    linhas_total: int = 0
    linhas_ok: int = 0
    status: str = "em_andamento"  # em_andamento, sucesso, falhou
    iniciada_em: Optional[str] = None
    salvo_em: Optional[str] = None
    erro_msg: Optional[str] = None


class PlanilhaCarregada(BaseModel):
    """Retorno unificado de `carregar_transferencias` (PRD §6.7.6)."""

    linhas: list[LinhaTransferencia]
    sha256: str
    caminho: Path

    model_config = {"arbitrary_types_allowed": True}
