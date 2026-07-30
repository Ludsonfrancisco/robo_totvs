from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import stat


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_RUNTIME_AGE = timedelta(days=1)
_PROCESSED_AGE = timedelta(days=45)
_QUARANTINE_AGE = timedelta(days=30)
_LOG_AGE = timedelta(days=14)


def _is_reparse(metadata) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _metadata(path: Path):
    try:
        return path.lstat()
    except OSError:
        return None


def _real_directory(path: Path) -> bool:
    metadata = _metadata(path)
    return bool(
        metadata is not None
        and stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not _is_reparse(metadata)
    )


def _regular_file(path: Path) -> bool:
    metadata = _metadata(path)
    return bool(
        metadata is not None
        and stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not _is_reparse(metadata)
        and getattr(metadata, "st_nlink", 1) == 1
    )


def _below(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return path != parent


def _resolve_candidate(path: Path, allowed_parent: Path) -> Path | None:
    metadata = _metadata(path)
    if metadata is None or stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
        return None
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if not _below(resolved, allowed_parent):
        return None
    return resolved


def _protected(path: Path, references: frozenset[Path]) -> bool:
    return any(
        path == reference
        or path in reference.parents
        or reference in path.parents
        for reference in references
    )


def _tree_entries(
    path: Path,
    *,
    allowed_parent: Path,
    references: frozenset[Path],
) -> list[Path] | None:
    resolved = _resolve_candidate(path, allowed_parent)
    if resolved is None or _protected(resolved, references):
        return None
    metadata = _metadata(path)
    if metadata is None:
        return None
    if stat.S_ISREG(metadata.st_mode):
        if getattr(metadata, "st_nlink", 1) != 1:
            return None
        return [path]
    if not stat.S_ISDIR(metadata.st_mode):
        return None
    try:
        children = list(path.iterdir())
    except OSError:
        return None
    entries = []
    for child in children:
        child_entries = _tree_entries(
            child,
            allowed_parent=allowed_parent,
            references=references,
        )
        if child_entries is None:
            return None
        entries.extend(child_entries)
    entries.append(path)
    return entries


def _remove_tree(
    path: Path,
    *,
    allowed_parent: Path,
    references: frozenset[Path],
) -> bool:
    entries = _tree_entries(
        path,
        allowed_parent=allowed_parent,
        references=references,
    )
    if entries is None:
        return False
    try:
        for entry in entries:
            resolved = _resolve_candidate(entry, allowed_parent)
            if resolved is None or _protected(resolved, references):
                return False
            metadata = entry.lstat()
            if stat.S_ISREG(metadata.st_mode):
                entry.unlink()
            elif stat.S_ISDIR(metadata.st_mode):
                entry.rmdir()
            else:
                return False
    except OSError:
        return False
    return True


def _older_than(path: Path, cutoff: datetime) -> bool:
    metadata = _metadata(path)
    if metadata is None:
        return False
    modified = datetime.fromtimestamp(metadata.st_mtime, timezone.utc)
    return modified < cutoff.astimezone(timezone.utc)


def _runtime_temporary(path: Path) -> bool:
    return (
        path.name.endswith(".tmp")
        or ".tmp." in path.name
        or path.name.startswith(".financeiro-medicao-")
    )


def _remove_old_files(
    directory: Path,
    *,
    cutoff: datetime,
    root: Path,
    references: frozenset[Path],
) -> None:
    if not _real_directory(directory):
        return
    try:
        candidates = list(directory.rglob("*"))
    except OSError:
        return
    for candidate in candidates:
        if (
            _regular_file(candidate)
            and _older_than(candidate, cutoff)
        ):
            _remove_tree(
                candidate,
                allowed_parent=root,
                references=references,
            )


def cleanup(
    resolved_root: Path,
    *,
    current_references,
    now: datetime | None = None,
) -> tuple[Path, ...]:
    """Apply retention only to enumerated operational namespaces."""
    root = Path(resolved_root)
    try:
        canonical_root = root.resolve(strict=True)
    except OSError as error:
        raise ValueError("Invalid retention root.") from error
    if (
        not root.is_absolute()
        or root != canonical_root
        or not _real_directory(root)
    ):
        raise ValueError("Retention root must be resolved.")

    references = set()
    for raw_reference in current_references:
        reference = Path(raw_reference)
        try:
            resolved = reference.resolve(strict=False)
            resolved.relative_to(root)
        except (OSError, ValueError) as error:
            raise ValueError("Reference outside retention root.") from error
        references.add(resolved)
    protected = frozenset(references)

    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("Retention time must include timezone.")

    removed = []
    runtime = root / "runtime"
    if _real_directory(runtime):
        try:
            runtime_entries = list(runtime.iterdir())
        except OSError:
            runtime_entries = []
        for candidate in runtime_entries:
            if (
                _runtime_temporary(candidate)
                and _older_than(candidate, moment - _RUNTIME_AGE)
                and _remove_tree(
                    candidate,
                    allowed_parent=runtime,
                    references=protected,
                )
            ):
                removed.append(candidate)
        for name in ("logs", "evidence"):
            before = set((runtime / name).rglob("*")) if _real_directory(runtime / name) else set()
            _remove_old_files(
                runtime / name,
                cutoff=moment - _LOG_AGE,
                root=runtime,
                references=protected,
            )
            removed.extend(path for path in before if not os.path.lexists(path))

    inbox = root / "inbox"
    if _real_directory(inbox):
        for candidate in list(inbox.iterdir()):
            marker = candidate / "processed.json"
            if (
                _real_directory(candidate)
                and _regular_file(marker)
                and _older_than(marker, moment - _PROCESSED_AGE)
                and _remove_tree(
                    candidate,
                    allowed_parent=inbox,
                    references=protected,
                )
            ):
                removed.append(candidate)

    quarantine = root / "quarantine"
    if _real_directory(quarantine):
        for candidate in list(quarantine.iterdir()):
            if (
                _older_than(candidate, moment - _QUARANTINE_AGE)
                and _remove_tree(
                    candidate,
                    allowed_parent=quarantine,
                    references=protected,
                )
            ):
                removed.append(candidate)
    return tuple(removed)
