from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .cycles import CycleWindow


FILTERS = {
    "sistema": "Consolidado",
    "executor": "Dmais",
    "modo_calculo": "Expurgados",
}


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _bundle_identity(manifest: dict) -> str:
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def build_bundle(
    *,
    runtime_root: Path,
    window: CycleWindow,
    summary_text: str,
    workbook_bytes: bytes,
    captured_at: datetime,
    before_publish: Callable[[], None] | None = None,
) -> Path:
    runtime_root = Path(runtime_root)
    runtime_dir = runtime_root / "runtime"
    inbox_dir = runtime_root / "inbox"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = runtime_dir / f"{uuid4()}.tmp"
    temp_dir.mkdir()

    summary_bytes = summary_text.encode("utf-8")
    summary_name = "summary.tsv"
    workbook_name = "atendimentos_indicadores.xlsx"
    (temp_dir / summary_name).write_bytes(summary_bytes)
    (temp_dir / workbook_name).write_bytes(workbook_bytes)

    manifest = {
        "schema_version": 1,
        "source": "LOGA",
        "cycle_start": window.cycle_start.isoformat(),
        "cycle_close": window.cycle_close.isoformat(),
        "query_start": window.query_start.isoformat(),
        "query_end": window.query_end.isoformat(),
        "captured_at": captured_at.isoformat(),
        "filters": FILTERS,
        "summary_file": summary_name,
        "summary_sha256": _digest(summary_bytes),
        "workbook_file": workbook_name,
        "workbook_sha256": _digest(workbook_bytes),
    }
    (temp_dir / "manifest.json").write_bytes(
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    )
    destination = inbox_dir / _bundle_identity(manifest)
    if before_publish:
        before_publish()
    if destination.exists():
        return destination
    os.replace(temp_dir, destination)
    return destination
