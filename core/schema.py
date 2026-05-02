from typing import Optional
from pydantic import BaseModel

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
