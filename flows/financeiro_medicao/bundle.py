import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from .cycles import CycleWindow
from .workbook import MAX_WORKBOOK_BYTES, SHEET_NAME, WorkbookInvalid, validate_workbook


_CHUNK_SIZE = 1024 * 1024
_WORKBOOK_NAME = "medicao_original.xlsx"
_MANIFEST_NAME = "manifest.json"


class BundleDurabilityError(RuntimeError):
    """A bundle is visible, but its directory entry may not be durable."""

    def __init__(self, published: Path):
        super().__init__("BUNDLE_DURABILITY_FAILED")
        self.published = Path(published)


def _validate_inputs(runtime_root, scheduled_for, started_at, finished_at, image_revision):
    root = Path(runtime_root)
    if root.name != "financeiro_medicao":
        raise ValueError("Diretório de execução inválido.")
    if not root.parent.is_dir():
        raise ValueError("Diretório pai de execução inválido.")
    if not isinstance(image_revision, str) or not image_revision.strip():
        raise ValueError("Revisão da imagem inválida.")
    timestamps = (scheduled_for, started_at, finished_at)
    if any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None for value in timestamps):
        raise ValueError("Datas de execução devem incluir fuso horário.")
    if not scheduled_for <= started_at <= finished_at:
        raise ValueError("Datas de execução estão fora de ordem.")
    return root, image_revision.strip()


def _copy_with_digest(source, destination):
    digest = hashlib.sha256()
    size = 0
    with Path(source).open("rb") as source_stream, destination.open("wb") as destination_stream:
        while chunk := source_stream.read(_CHUNK_SIZE):
            if size + len(chunk) > MAX_WORKBOOK_BYTES:
                raise WorkbookInvalid("Arquivo de medição inválido.")
            destination_stream.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        destination_stream.flush()
        os.fsync(destination_stream.fileno())
    return size, digest.hexdigest()


def _write_manifest(path, manifest):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path):
    """Persist directory entries on POSIX; Windows' os module does not support it."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mkdir_durable(path):
    try:
        path.mkdir()
    except FileExistsError:
        return
    _fsync_directory(path)
    _fsync_directory(path.parent)


def _remove_temp(temp_dir, runtime_dir):
    try:
        resolved_temp = temp_dir.resolve()
        resolved_runtime = runtime_dir.resolve()
        resolved_temp.relative_to(resolved_runtime)
    except (OSError, ValueError):
        return
    if temp_dir.exists() and temp_dir.parent.resolve() == resolved_runtime and temp_dir.name.endswith(".tmp"):
        rmtree(temp_dir)


def build_bundle(*, runtime_root: Path, source: Path, window: CycleWindow,
                 scheduled_for: datetime, started_at: datetime, finished_at: datetime,
                 image_revision: str) -> Path:
    """Validate a copied workbook and atomically publish its immutable inbox bundle."""
    root, image_revision = _validate_inputs(
        runtime_root, scheduled_for, started_at, finished_at, image_revision
    )
    runtime_dir = root / "runtime"
    inbox_dir = root / "inbox"
    _mkdir_durable(root)
    _mkdir_durable(runtime_dir)
    _mkdir_durable(inbox_dir)

    run_id = uuid4().hex
    temp_dir = runtime_dir / f"{run_id}.tmp"
    published_dir = inbox_dir / run_id
    temp_dir.mkdir(mode=0o700)
    try:
        workbook_path = temp_dir / _WORKBOOK_NAME
        workbook_size, workbook_sha256 = _copy_with_digest(source, workbook_path)
        workbook = validate_workbook(workbook_path, window.query_start, window.query_end)
        if workbook.size != workbook_size:
            raise OSError("Tamanho da cópia de medição inconsistente.")

        manifest = {
            "schema_version": 1,
            "source": "LOGA",
            "flow": "financeiro_medicao",
            "run_id": run_id,
            "cycle_id": window.cycle_id,
            "cycle_start": window.cycle_start.isoformat(),
            "cycle_close": window.cycle_close.isoformat(),
            "mode": window.mode,
            "scheduled_for": scheduled_for.isoformat(),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "query_start": window.query_start.isoformat(),
            "query_end": window.query_end.isoformat(),
            "image_revision": image_revision,
            "status": "success",
            "workbook_file": _WORKBOOK_NAME,
            "workbook_size": workbook_size,
            "workbook_sha256": workbook_sha256,
            "row_count": workbook.row_count,
            "sheet_name": SHEET_NAME,
            "headers": list(workbook.headers),
        }
        _write_manifest(temp_dir / _MANIFEST_NAME, manifest)
        _fsync_directory(temp_dir)
        _fsync_directory(runtime_dir)
        os.replace(temp_dir, published_dir)
        try:
            _fsync_directory(inbox_dir)
            _fsync_directory(runtime_dir)
        except BaseException as error:
            raise BundleDurabilityError(published_dir) from error
        return published_dir
    except BaseException:
        _remove_temp(temp_dir, runtime_dir)
        raise
