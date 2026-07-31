import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from shutil import rmtree
import stat
from uuid import uuid4

from flows.common.safe_snapshot import (
    close as close_snapshot,
    is_current as snapshot_is_current,
    open_directory_snapshot,
    private_file,
    read_file,
)

from .cycles import CycleWindow
from .workbook import (
    MAX_WORKBOOK_BYTES,
    REQUIRED_HEADERS,
    SHEET_NAME,
    WorkbookInvalid,
    validate_workbook,
)


_CHUNK_SIZE = 1024 * 1024
_WORKBOOK_NAME = "medicao_original.xlsx"
_MANIFEST_NAME = "manifest.json"
_COMMIT_NAME = "publication.json"
_DETERMINISTIC_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_COMMIT_KEYS = {
    "schema_version",
    "status",
    "run_id",
    "manifest_sha256",
    "workbook_size",
    "workbook_sha256",
}
_MANIFEST_KEYS = {
    "schema_version",
    "source",
    "flow",
    "run_id",
    "cycle_id",
    "cycle_start",
    "cycle_close",
    "mode",
    "scheduled_for",
    "started_at",
    "finished_at",
    "query_start",
    "query_end",
    "image_revision",
    "status",
    "workbook_file",
    "workbook_size",
    "workbook_sha256",
    "row_count",
    "sheet_name",
    "headers",
}


class BundleDurabilityError(RuntimeError):
    """A bundle is visible, but its directory entry may not be durable."""

    def __init__(self, published: Path):
        super().__init__("BUNDLE_DURABILITY_FAILED")
        self.published = Path(published)


class BundleCollisionError(RuntimeError):
    """A deterministic run id points to an inconsistent publication."""

    def __init__(self, published: Path):
        super().__init__("BUNDLE_COLLISION")
        self.published = Path(published)


@dataclass(frozen=True)
class PublishedBundleDetails:
    path: Path
    manifest: dict
    workbook_size: int
    workbook_sha256: str


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
    path = Path(path)
    _require_real_directory(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        _require_real_directory(path)
        return
    _require_real_directory(path)
    _fsync_directory(path)
    _fsync_directory(path.parent)


def _require_real_directory(path):
    try:
        metadata = Path(path).lstat()
    except OSError as error:
        raise ValueError("Invalid bundle directory.") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or bool(
            getattr(metadata, "st_file_attributes", 0)
            & _FILE_ATTRIBUTE_REPARSE_POINT
        )
    ):
        raise ValueError("Invalid bundle directory.")


def _remove_temp(temp_dir, runtime_dir):
    try:
        resolved_temp = temp_dir.resolve()
        resolved_runtime = runtime_dir.resolve()
        resolved_temp.relative_to(resolved_runtime)
    except (OSError, ValueError):
        return
    if temp_dir.exists() and temp_dir.parent.resolve() == resolved_runtime and temp_dir.name.endswith(".tmp"):
        rmtree(temp_dir)


def validate_manifest_snapshot(
    manifest,
    *,
    run_id,
    window,
    scheduled_for,
    expected_result=None,
):
    expected = {
        "schema_version": 1,
        "source": "LOGA",
        "flow": "financeiro_medicao",
        "run_id": run_id,
        "cycle_id": window.cycle_id,
        "cycle_start": window.cycle_start.isoformat(),
        "cycle_close": window.cycle_close.isoformat(),
        "mode": window.mode,
        "scheduled_for": scheduled_for.isoformat(),
        "query_start": window.query_start.isoformat(),
        "query_end": window.query_end.isoformat(),
        "status": "success",
        "workbook_file": _WORKBOOK_NAME,
        "sheet_name": SHEET_NAME,
        "headers": list(REQUIRED_HEADERS),
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != _MANIFEST_KEYS
        or any(
            manifest.get(key) != value
            for key, value in expected.items()
        )
        or not isinstance(manifest.get("image_revision"), str)
        or not manifest["image_revision"].strip()
        or type(manifest.get("workbook_size")) is not int
        or manifest["workbook_size"] <= 0
        or not isinstance(manifest.get("workbook_sha256"), str)
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            manifest["workbook_sha256"],
        )
        or type(manifest.get("row_count")) is not int
        or manifest["row_count"] <= 0
    ):
        raise ValueError("Manifest mismatch.")
    if expected_result is not None and (
        not isinstance(expected_result, dict)
        or expected_result.get("success") is not True
        or expected_result.get("error_code") != ""
        or expected_result.get("run_id") != manifest.get("run_id")
        or expected_result.get("cycle_id")
        != manifest.get("cycle_id")
        or expected_result.get("mode") != manifest.get("mode")
        or expected_result.get("started_at")
        != manifest.get("started_at")
        or expected_result.get("finished_at")
        != manifest.get("finished_at")
    ):
        raise ValueError("Published result mismatch.")
    try:
        started_at = datetime.fromisoformat(manifest["started_at"])
        finished_at = datetime.fromisoformat(manifest["finished_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Manifest timestamps mismatch.") from error
    if (
        started_at.tzinfo is None
        or started_at.utcoffset() is None
        or finished_at.tzinfo is None
        or finished_at.utcoffset() is None
        or not scheduled_for <= started_at <= finished_at
    ):
        raise ValueError("Manifest timestamps mismatch.")
    return dict(manifest)


def _inspect_bundle(
    *,
    runtime_root: Path,
    parent: Path,
    run_id: str,
    window: CycleWindow,
    scheduled_for: datetime,
    expected_result: dict | None = None,
    directory_name: str | None = None,
) -> PublishedBundleDetails | None:
    directory_name = directory_name or run_id
    published = Path(parent) / directory_name
    opened = None
    try:
        if not _DETERMINISTIC_RUN_ID.fullmatch(run_id):
            raise ValueError("Invalid deterministic bundle.")
        opened = open_directory_snapshot(
            Path(parent),
            directory_name,
            (_MANIFEST_NAME, _WORKBOOK_NAME),
        )
        if opened is None:
            return None
        manifest = json.loads(
            read_file(
                opened,
                _MANIFEST_NAME,
                max_bytes=1024 * 1024,
            ).decode("utf-8")
        )
        manifest = validate_manifest_snapshot(
            manifest,
            run_id=run_id,
            window=window,
            scheduled_for=scheduled_for,
            expected_result=expected_result,
        )
        with private_file(
            opened,
            _WORKBOOK_NAME,
            runtime_dir=Path(runtime_root) / "runtime",
            max_bytes=MAX_WORKBOOK_BYTES,
            prefix=".financeiro-medicao-validate-",
            suffix=".xlsx",
        ) as private:
            workbook = validate_workbook(
                private.stream,
                window.query_start,
                window.query_end,
            )
            if (
                manifest.get("workbook_size") != private.size
                or manifest.get("workbook_sha256")
                != private.sha256
                or manifest.get("row_count")
                != workbook.row_count
                or workbook.size != private.size
                or list(workbook.headers)
                != list(REQUIRED_HEADERS)
            ):
                raise ValueError("Workbook mismatch.")
            if not snapshot_is_current(opened):
                raise ValueError("Deterministic bundle changed.")
            details = PublishedBundleDetails(
                path=published,
                manifest=manifest,
                workbook_size=private.size,
                workbook_sha256=private.sha256,
            )
    except Exception as error:
        raise BundleCollisionError(published) from error
    finally:
        close_snapshot(opened)
    return details


def inspect_published_bundle(
    *,
    runtime_root: Path,
    run_id: str,
    window: CycleWindow,
    scheduled_for: datetime,
    expected_result: dict | None = None,
) -> PublishedBundleDetails | None:
    return _inspect_bundle(
        runtime_root=runtime_root,
        parent=Path(runtime_root) / "inbox",
        run_id=run_id,
        window=window,
        scheduled_for=scheduled_for,
        expected_result=expected_result,
    )


def publication_proof_path(
    runtime_root: Path,
    run_id: str,
) -> Path:
    if not _DETERMINISTIC_RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid deterministic bundle.")
    return (
        Path(runtime_root)
        / "runtime"
        / "proofs"
        / run_id
    )


def inspect_publication_proof(
    *,
    runtime_root: Path,
    run_id: str,
    window: CycleWindow,
    scheduled_for: datetime,
    expected_result: dict | None = None,
) -> PublishedBundleDetails | None:
    return _inspect_bundle(
        runtime_root=runtime_root,
        parent=Path(runtime_root) / "runtime" / "proofs",
        run_id=run_id,
        window=window,
        scheduled_for=scheduled_for,
        expected_result=expected_result,
    )


def _manifest_sha256(manifest: dict) -> str:
    encoded = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def publication_pending_path(
    runtime_root: Path,
    run_id: str,
) -> Path:
    if not _DETERMINISTIC_RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid deterministic bundle.")
    return Path(runtime_root) / "inbox" / f".{run_id}.pending"


def _write_publication_commit(
    details: PublishedBundleDetails,
) -> None:
    target = details.path / _COMMIT_NAME
    temporary = details.path / (
        f".{_COMMIT_NAME}.{uuid4().hex}.tmp"
    )
    payload = {
        "schema_version": 1,
        "status": "published",
        "run_id": details.manifest["run_id"],
        "manifest_sha256": _manifest_sha256(details.manifest),
        "workbook_size": details.workbook_size,
        "workbook_sha256": details.workbook_sha256,
    }
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as stream:
            descriptor = None
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_directory(details.path)
        _fsync_directory(details.path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _read_publication_commit(
    runtime_root: Path,
    run_id: str,
) -> dict | None:
    opened = open_directory_snapshot(
        Path(runtime_root) / "runtime" / "proofs",
        run_id,
        (_COMMIT_NAME,),
        missing_files_none=True,
    )
    if opened is None:
        return None
    try:
        commit = json.loads(
            read_file(
                opened,
                _COMMIT_NAME,
                max_bytes=64 * 1024,
            ).decode("utf-8")
        )
        if not snapshot_is_current(opened):
            raise ValueError("Publication commit changed.")
        if not isinstance(commit, dict):
            raise ValueError("Invalid publication commit.")
        return commit
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError("Invalid publication commit.") from error
    finally:
        close_snapshot(opened)


def inspect_committed_publication(
    *,
    runtime_root: Path,
    run_id: str,
    window: CycleWindow,
    scheduled_for: datetime,
    expected_result: dict | None = None,
) -> PublishedBundleDetails | None:
    proof = inspect_publication_proof(
        runtime_root=runtime_root,
        run_id=run_id,
        window=window,
        scheduled_for=scheduled_for,
        expected_result=expected_result,
    )
    if proof is None:
        return None
    try:
        commit = _read_publication_commit(runtime_root, run_id)
        if commit is None:
            return None
        expected = {
            "schema_version": 1,
            "status": "published",
            "run_id": run_id,
            "manifest_sha256": _manifest_sha256(proof.manifest),
            "workbook_size": proof.workbook_size,
            "workbook_sha256": proof.workbook_sha256,
        }
        if set(commit) != _COMMIT_KEYS or commit != expected:
            raise ValueError("Publication commit mismatch.")
        pending = publication_pending_path(runtime_root, run_id)
        published = Path(runtime_root) / "inbox" / run_id
        pending_details = _inspect_bundle(
            runtime_root=runtime_root,
            parent=Path(runtime_root) / "inbox",
            directory_name=pending.name,
            run_id=run_id,
            window=window,
            scheduled_for=scheduled_for,
            expected_result=expected_result,
        )
        if pending_details is not None:
            if published.exists():
                raise ValueError("Publication state collision.")
            os.replace(pending, published)
            _fsync_directory(published.parent)
            _fsync_directory(Path(runtime_root) / "runtime")
    except BundleCollisionError:
        raise
    except Exception as error:
        raise BundleCollisionError(proof.path) from error
    return proof


def _publish_proof(
    *,
    runtime_root: Path,
    runtime_dir: Path,
    run_id: str,
    manifest: dict,
    workbook_source: Path,
) -> Path:
    proofs_dir = runtime_dir / "proofs"
    proofs_existed = proofs_dir.exists()
    _mkdir_durable(proofs_dir)
    proof = publication_proof_path(runtime_root, run_id)
    temporary = runtime_dir / (
        f".{run_id}.{uuid4().hex}.proof.tmp"
    )
    temporary.mkdir(mode=0o700)
    try:
        workbook_size, workbook_sha256 = _copy_with_digest(
            workbook_source,
            temporary / _WORKBOOK_NAME,
        )
        if (
            workbook_size != manifest["workbook_size"]
            or workbook_sha256 != manifest["workbook_sha256"]
        ):
            raise ValueError("Publication proof mismatch.")
        _write_manifest(temporary / _MANIFEST_NAME, manifest)
        _fsync_directory(temporary)
        _fsync_directory(runtime_dir)
        os.replace(temporary, proof)
        _fsync_directory(proofs_dir)
        _fsync_directory(runtime_dir)
        return proof
    finally:
        _remove_temp(temporary, runtime_dir)
        if not proofs_existed and not proof.exists():
            try:
                proofs_dir.rmdir()
                _fsync_directory(runtime_dir)
            except OSError:
                pass


def _stage_from_proof(
    *,
    runtime_root: Path,
    runtime_dir: Path,
    inbox_dir: Path,
    details: PublishedBundleDetails,
    window: CycleWindow,
    scheduled_for: datetime,
) -> Path:
    run_id = details.manifest["run_id"]
    pending = publication_pending_path(runtime_root, run_id)
    existing = _inspect_bundle(
        runtime_root=runtime_root,
        parent=inbox_dir,
        directory_name=pending.name,
        run_id=run_id,
        window=window,
        scheduled_for=scheduled_for,
    )
    if existing is not None:
        if (
            existing.manifest != details.manifest
            or existing.workbook_size != details.workbook_size
            or existing.workbook_sha256
            != details.workbook_sha256
        ):
            raise BundleCollisionError(pending)
        return pending

    temporary = runtime_dir / (
        f".{run_id}.{uuid4().hex}.delivery.tmp"
    )
    temporary.mkdir(mode=0o700)
    try:
        workbook_size, workbook_sha256 = _copy_with_digest(
            details.path / _WORKBOOK_NAME,
            temporary / _WORKBOOK_NAME,
        )
        if (
            workbook_size != details.workbook_size
            or workbook_sha256 != details.workbook_sha256
        ):
            raise ValueError("Staged publication mismatch.")
        _write_manifest(
            temporary / _MANIFEST_NAME,
            details.manifest,
        )
        _fsync_directory(temporary)
        _fsync_directory(runtime_dir)
        os.replace(temporary, pending)
        _fsync_directory(inbox_dir)
        _fsync_directory(runtime_dir)
        return pending
    finally:
        _remove_temp(temporary, runtime_dir)


def _commit_staged_publication(
    *,
    runtime_root: Path,
    runtime_dir: Path,
    inbox_dir: Path,
    details: PublishedBundleDetails,
    pending: Path,
) -> Path:
    published = inbox_dir / details.manifest["run_id"]
    _write_publication_commit(details)
    os.replace(pending, published)
    try:
        _fsync_directory(inbox_dir)
        _fsync_directory(runtime_dir)
    except Exception as error:
        raise BundleDurabilityError(published) from error
    return published


def validate_published_bundle(
    *,
    runtime_root: Path,
    run_id: str,
    window: CycleWindow,
    scheduled_for: datetime,
    expected_result: dict | None = None,
) -> Path | None:
    details = inspect_published_bundle(
        runtime_root=runtime_root,
        run_id=run_id,
        window=window,
        scheduled_for=scheduled_for,
        expected_result=expected_result,
    )
    return None if details is None else details.path


def build_bundle(*, runtime_root: Path, source: Path, window: CycleWindow,
                 scheduled_for: datetime, started_at: datetime, finished_at: datetime,
                 image_revision: str, run_id: str | None = None) -> Path:
    """Validate a copied workbook and atomically publish its immutable inbox bundle."""
    root, image_revision = _validate_inputs(
        runtime_root, scheduled_for, started_at, finished_at, image_revision
    )
    runtime_dir = root / "runtime"
    inbox_dir = root / "inbox"
    _mkdir_durable(root)
    _mkdir_durable(runtime_dir)
    _mkdir_durable(inbox_dir)

    if run_id is not None and not _DETERMINISTIC_RUN_ID.fullmatch(run_id):
        raise ValueError("Identificador determinístico inválido.")
    run_id = run_id or uuid4().hex
    temp_dir = runtime_dir / f"{run_id}.tmp"
    published_dir = inbox_dir / run_id
    existing_details = inspect_published_bundle(
        runtime_root=root,
        run_id=run_id,
        window=window,
        scheduled_for=scheduled_for,
    )
    proof_details = inspect_publication_proof(
        runtime_root=root,
        run_id=run_id,
        window=window,
        scheduled_for=scheduled_for,
    )
    committed_details = inspect_committed_publication(
        runtime_root=root,
        run_id=run_id,
        window=window,
        scheduled_for=scheduled_for,
    )
    if committed_details is not None:
        return (
            published_dir
            if published_dir.exists()
            else committed_details.path
        )
    if proof_details is not None:
        if existing_details is not None:
            _write_publication_commit(proof_details)
            return existing_details.path
        pending = _stage_from_proof(
            runtime_root=root,
            runtime_dir=runtime_dir,
            inbox_dir=inbox_dir,
            details=proof_details,
            window=window,
            scheduled_for=scheduled_for,
        )
        return _commit_staged_publication(
            runtime_root=root,
            runtime_dir=runtime_dir,
            inbox_dir=inbox_dir,
            details=proof_details,
            pending=pending,
        )
    if existing_details is not None:
        proof = _publish_proof(
            runtime_root=root,
            runtime_dir=runtime_dir,
            run_id=run_id,
            manifest=existing_details.manifest,
            workbook_source=(
                existing_details.path / _WORKBOOK_NAME
            ),
        )
        proof_details = PublishedBundleDetails(
            path=proof,
            manifest=existing_details.manifest,
            workbook_size=existing_details.workbook_size,
            workbook_sha256=existing_details.workbook_sha256,
        )
        _write_publication_commit(proof_details)
        return existing_details.path
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
        proof = _publish_proof(
            runtime_root=root,
            runtime_dir=runtime_dir,
            run_id=run_id,
            manifest=manifest,
            workbook_source=workbook_path,
        )
        proof_details = PublishedBundleDetails(
            path=proof,
            manifest=manifest,
            workbook_size=workbook_size,
            workbook_sha256=workbook_sha256,
        )
        pending = publication_pending_path(root, run_id)
        os.replace(temp_dir, pending)
        _fsync_directory(inbox_dir)
        _fsync_directory(runtime_dir)
        return _commit_staged_publication(
            runtime_root=root,
            runtime_dir=runtime_dir,
            inbox_dir=inbox_dir,
            details=proof_details,
            pending=pending,
        )
    except BaseException:
        _remove_temp(temp_dir, runtime_dir)
        raise
