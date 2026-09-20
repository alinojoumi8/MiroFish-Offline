"""Crash-safe state-file replacement and per-resource synchronization."""

import json
import errno
import os
import tempfile
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback remains thread-safe
    fcntl = None


_registry_guard = threading.Lock()
_resource_locks = weakref.WeakValueDictionary()
_process_lock_state = threading.local()
_UNSUPPORTED_DIRECTORY_SYNC = {
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
}


def _lock_for(key):
    normalized = str(key)
    with _registry_guard:
        lock = _resource_locks.get(normalized)
        if lock is None:
            lock = threading.RLock()
            _resource_locks[normalized] = lock
        return lock


@contextmanager
def _advisory_lock(lock_path):
    if lock_path is None or fcntl is None:
        yield
        return
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    held = getattr(_process_lock_state, "held", None)
    if held is None:
        held = {}
        _process_lock_state.held = held
    normalized = str(path.resolve())
    if normalized in held:
        held[normalized][1] += 1
        try:
            yield
        finally:
            held[normalized][1] -= 1
        return
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        held[normalized] = [handle, 1]
        yield
    finally:
        held.pop(normalized, None)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def resource_lock(key, *, lock_path=None):
    """Layer a keyed in-process RLock with an optional advisory file lock."""
    lock = _lock_for(key)
    with lock:
        with _advisory_lock(lock_path):
            yield


def atomic_write_text(path, content, encoding="utf-8"):
    """Durably replace a text file using a temporary file in the same directory."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        # Persist the directory entry where supported.
        try:
            directory_fd = os.open(
                destination.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
        except OSError as error:
            if error.errno in _UNSUPPORTED_DIRECTORY_SYNC:
                return
            raise
        try:
            try:
                os.fsync(directory_fd)
            except OSError as error:
                if error.errno not in _UNSUPPORTED_DIRECTORY_SYNC:
                    raise
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path, value, *, ensure_ascii=False, indent=2):
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=ensure_ascii, indent=indent),
    )
