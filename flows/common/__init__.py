"""Shared primitives for browser-backed automation flows."""

from .locks import LockUnavailable, file_lock

__all__ = ["LockUnavailable", "file_lock"]
