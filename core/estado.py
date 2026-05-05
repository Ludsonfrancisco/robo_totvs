import json
from pathlib import Path
from typing import Dict
from datetime import datetime

from pydantic import BaseModel

from core.config import settings
from core.schema import CheckpointItem, CheckpointTransferenciaMultipla

class Checkpoint(BaseModel):
    items: Dict[str, CheckpointItem] = {}

def get_checkpoint_path() -> Path:
    hoje_str = datetime.now().strftime("%Y-%m-%d")
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return settings.state_dir / f"checkpoint_{hoje_str}.json"

def carregar_checkpoint() -> Checkpoint:
    path = get_checkpoint_path()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return Checkpoint(**data)
    return Checkpoint()

def salvar_checkpoint(checkpoint: Checkpoint) -> None:
    path = get_checkpoint_path()
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(checkpoint.model_dump_json(indent=2))
    
    # Atomic rename
    temp_path.replace(path)

def get_checkpoint_trans_multipla_path() -> Path:
    hoje_str = datetime.now().strftime("%Y-%m-%d")
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return settings.state_dir / f"transferencia_multipla_{hoje_str}.json"

def carregar_checkpoint_trans_multipla() -> CheckpointTransferenciaMultipla | None:
    path = get_checkpoint_trans_multipla_path()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return CheckpointTransferenciaMultipla(**data)
    return None

def salvar_checkpoint_trans_multipla(checkpoint: CheckpointTransferenciaMultipla) -> None:
    path = get_checkpoint_trans_multipla_path()
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(checkpoint.model_dump_json(indent=2))
    
    # Atomic rename
    temp_path.replace(path)
