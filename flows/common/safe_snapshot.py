import hashlib
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import stat
import tempfile

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _identity(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mode,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _is_reparse(metadata):
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _node_identity(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
    )


def _open_posix_entry(name, *, directory_fd, directory):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        metadata = os.fstat(descriptor)
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        entry = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not expected_type(metadata.st_mode)
            or _identity(entry) != _identity(metadata)
        ):
            raise ValueError("Snapshot entry changed.")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, _identity(metadata)


def _open_posix(parent: Path, name: str, filenames):
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_fd = os.open(parent, flags)
    except FileNotFoundError:
        return None
    descriptors = [parent_fd]
    try:
        try:
            directory_fd, directory_identity = _open_posix_entry(
                name,
                directory_fd=parent_fd,
                directory=True,
            )
        except FileNotFoundError:
            os.close(parent_fd)
            return None
        descriptors.append(directory_fd)
        files = {}
        for filename in filenames:
            descriptor, identity = _open_posix_entry(
                filename,
                directory_fd=directory_fd,
                directory=False,
            )
            descriptors.append(descriptor)
            files[filename] = {
                "descriptor": descriptor,
                "identity": identity,
            }
        return {
            "descriptors": descriptors,
            "parent": parent,
            "parent_fd": parent_fd,
            "parent_identity": _identity(os.fstat(parent_fd)),
            "name": name,
            "directory_fd": directory_fd,
            "directory_identity": directory_identity,
            "files": files,
            "posix": True,
        }
    except BaseException:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _open_windows(
    parent: Path,
    name: str,
    filenames,
    *,
    missing_files_none=False,
):
    try:
        parent_stat = parent.lstat()
    except FileNotFoundError:
        return None
    if (
        stat.S_ISLNK(parent_stat.st_mode)
        or not stat.S_ISDIR(parent_stat.st_mode)
        or _is_reparse(parent_stat)
    ):
        raise ValueError("Invalid snapshot directory.")
    directory = parent / name
    try:
        directory_stat = directory.lstat()
    except FileNotFoundError:
        return None
    if (
        stat.S_ISLNK(directory_stat.st_mode)
        or not stat.S_ISDIR(directory_stat.st_mode)
        or _is_reparse(directory_stat)
    ):
        raise ValueError("Invalid snapshot directory.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    descriptors = []
    files = {}
    try:
        for filename in filenames:
            path = directory / filename
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                if missing_files_none:
                    close(
                        {"descriptors": descriptors}
                    )
                    return None
                raise
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or _is_reparse(metadata)
            ):
                raise ValueError("Invalid snapshot file.")
            descriptor = os.open(path, flags)
            descriptors.append(descriptor)
            identity = _identity(os.fstat(descriptor))
            if identity != _identity(metadata):
                raise ValueError("Snapshot entry changed.")
            files[filename] = {
                "descriptor": descriptor,
                "identity": identity,
                "path": path,
            }
        return {
            "descriptors": descriptors,
            "parent": parent,
            "parent_identity": _identity(parent_stat),
            "directory": directory,
            "directory_identity": _identity(directory_stat),
            "files": files,
            "posix": False,
        }
    except BaseException:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def open_directory_snapshot(
    parent: Path,
    name: str,
    filenames,
    *,
    missing_files_none=False,
):
    parent = Path(parent)
    filenames = tuple(filenames)
    return (
        _open_posix_optional(
            parent,
            name,
            filenames,
            missing_files_none=missing_files_none,
        )
        if os.name == "posix"
        else _open_windows(
            parent,
            name,
            filenames,
            missing_files_none=missing_files_none,
        )
    )


def _open_posix_optional(
    parent,
    name,
    filenames,
    *,
    missing_files_none,
):
    if not missing_files_none:
        return _open_posix(parent, name, filenames)
    try:
        return _open_posix(parent, name, filenames)
    except FileNotFoundError:
        return None


@contextmanager
def directory_snapshot(
    parent: Path,
    name: str,
    filenames,
    *,
    missing_files_none=False,
):
    snapshot = open_directory_snapshot(
        parent,
        name,
        filenames,
        missing_files_none=missing_files_none,
    )
    try:
        yield snapshot
    finally:
        close(snapshot)


def read_file(snapshot, filename: str, *, max_bytes: int) -> bytes:
    descriptor = snapshot["files"][filename]["descriptor"]
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    size = 0
    while chunk := os.read(descriptor, min(1024 * 1024, max_bytes + 1)):
        size += len(chunk)
        if size > max_bytes:
            raise ValueError("Snapshot file is too large.")
        chunks.append(chunk)
    return b"".join(chunks)


@dataclass(frozen=True)
class PrivateFile:
    stream: object
    path: Path
    size: int
    sha256: str


@contextmanager
def private_file(
    snapshot,
    filename: str,
    *,
    runtime_dir: Path,
    max_bytes: int,
    prefix: str,
    suffix: str,
):
    runtime_dir = Path(runtime_dir)
    try:
        runtime_stat = runtime_dir.lstat()
    except FileNotFoundError as error:
        raise ValueError(
            "Invalid private runtime directory."
        ) from error
    if (
        stat.S_ISLNK(runtime_stat.st_mode)
        or not stat.S_ISDIR(runtime_stat.st_mode)
        or _is_reparse(runtime_stat)
    ):
        raise ValueError("Invalid private runtime directory.")
    source = snapshot["files"][filename]["descriptor"]
    stream = tempfile.NamedTemporaryFile(
        mode="w+b",
        prefix=prefix,
        suffix=suffix,
        dir=runtime_dir,
        delete=False,
    )
    path = Path(stream.name)
    digest = hashlib.sha256()
    size = 0
    try:
        os.chmod(path, 0o600)
        os.lseek(source, 0, os.SEEK_SET)
        while chunk := os.read(source, 1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("Snapshot file is too large.")
            stream.write(chunk)
            digest.update(chunk)
        stream.flush()
        stream.seek(0)
        if (
            _node_identity(runtime_dir.lstat())
            != _node_identity(runtime_stat)
        ):
            raise ValueError("Private runtime directory changed.")
        yield PrivateFile(
            stream=stream,
            path=path,
            size=size,
            sha256=digest.hexdigest(),
        )
    finally:
        stream.close()
        path.unlink(missing_ok=True)


def is_current(snapshot) -> bool:
    try:
        if (
            _identity(snapshot["parent"].lstat())
            != snapshot["parent_identity"]
        ):
            return False
        if snapshot["posix"]:
            if (
                _identity(os.fstat(snapshot["parent_fd"]))
                != snapshot["parent_identity"]
                or _identity(os.fstat(snapshot["directory_fd"]))
                != snapshot["directory_identity"]
                or _identity(
                    os.stat(
                        snapshot["name"],
                        dir_fd=snapshot["parent_fd"],
                        follow_symlinks=False,
                    )
                )
                != snapshot["directory_identity"]
            ):
                return False
            for filename, opened in snapshot["files"].items():
                if (
                    _identity(os.fstat(opened["descriptor"]))
                    != opened["identity"]
                    or _identity(
                        os.stat(
                            filename,
                            dir_fd=snapshot["directory_fd"],
                            follow_symlinks=False,
                        )
                    )
                    != opened["identity"]
                ):
                    return False
            return True
        if (
            _identity(snapshot["directory"].lstat())
            != snapshot["directory_identity"]
        ):
            return False
        return all(
            _identity(os.fstat(opened["descriptor"]))
            == opened["identity"]
            == _identity(opened["path"].lstat())
            for opened in snapshot["files"].values()
        )
    except OSError:
        return False


def close(snapshot) -> None:
    if snapshot is None:
        return
    first_error = None
    for descriptor in reversed(snapshot["descriptors"]):
        try:
            os.close(descriptor)
        except OSError as error:
            first_error = first_error or error
    if first_error is not None:
        raise first_error
