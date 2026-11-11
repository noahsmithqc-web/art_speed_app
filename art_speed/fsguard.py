"""Folder checks and writes with a time limit.

A disconnected network drive can make a plain os call hang for a minute or more.
Every call here runs on its own background thread; if it hasn't finished within
the limit, FsTimeout is raised and the user sees an error instead of a frozen spinner.
(A hung call can't be cancelled; its thread is left to finish on its own.)
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, TypeVar

from art_speed import config
from art_speed.log import get_logger

log = get_logger(__name__)
T = TypeVar("T")


class FsTimeout(TimeoutError):
    def __init__(self, what: str, timeout: float):
        self.what = what
        self.timeout = timeout
        super().__init__(f"{what} did not respond within {timeout:g} s")


def guarded(fn: Callable[..., T], *args: Any, timeout: float | None = None,
            what: str = "file operation", **kwargs: Any) -> T:
    """Run fn(*args, **kwargs) with a time limit. Exceptions from fn pass through unchanged."""
    limit = config.FS_TIMEOUT_S if timeout is None else timeout
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # re-raised on the calling thread
            box["error"] = exc

    worker = threading.Thread(target=run, name=f"fsguard: {what}", daemon=True)
    worker.start()
    worker.join(limit)
    if worker.is_alive():
        log.warning("timed out after %gs: %s", limit, what)
        raise FsTimeout(what, limit)
    if "error" in box:
        raise box["error"]
    return box.get("value")  # type: ignore[return-value]


def exists(path: str | os.PathLike, timeout: float | None = None) -> bool:
    return guarded(os.path.exists, path, timeout=timeout, what=f"checking {path}")


def is_dir(path: str | os.PathLike, timeout: float | None = None) -> bool:
    return guarded(os.path.isdir, path, timeout=timeout, what=f"checking {path}")


def _probe(folder: Path) -> None:
    probe = folder / f".speed_write_test_{uuid.uuid4().hex}.tmp"
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("ok")
        fh.flush()
        os.fsync(fh.fileno())
    probe.unlink()


def check_writable(folder: str | os.PathLike, timeout: float | None = None) -> None:
    """Write and delete a small test file. Raises OSError (or FsTimeout) if that fails."""
    guarded(_probe, Path(folder), timeout=timeout, what=f"writing to {folder}")


def make_dir(path: str | os.PathLike, timeout: float | None = None) -> None:
    """Create exactly one folder. Never creates parents; fails if it already exists."""
    guarded(os.mkdir, path, timeout=timeout, what=f"creating {path}")
