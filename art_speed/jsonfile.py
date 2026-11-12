"""Reading and writing the app's JSON files safely.

Two rules everywhere the app saves JSON:

* Nothing touches disk until the content is valid. The text is built and checked
  in memory first, so a bad value can never leave a half-written file behind.
* A save is atomic: write a temporary file next to the target, flush it to disk,
  close it, then rename it over the target. A reader either sees the old file or
  the new one, never a partial one. The last known good copy is kept as <name>.bak
  — the previous content when there was some, otherwise a copy of this first save,
  so a file always has a backup behind it.

On reading, a corrupt file is set aside as <name>.bad and the .bak copy is used
instead. The caller is told which file the data came from so it can tell the user.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from art_speed import fsguard
from art_speed.log import get_logger

log = get_logger(__name__)

Validator = Callable[[Any], Any]


class JsonFileError(Exception):
    """The file could not be read, and no usable backup existed."""


@dataclass
class Loaded:
    data: Any
    source: str                       # "file" | "backup" | "missing"
    notes: list[str] = field(default_factory=list)

    @property
    def recovered(self) -> bool:
        return self.source == "backup"


def _bak(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _read_one(path: Path, validate: Validator | None) -> Any:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    return validate(data) if validate else data


def _set_aside(path: Path) -> Path | None:
    """Rename a corrupt file to <name>.bad (keeping any older .bad aside)."""
    if not path.exists():
        return None
    bad = path.with_suffix(path.suffix + ".bad")
    try:
        if bad.exists():
            bad.replace(bad.with_name(f"{bad.name}.{int(time.time())}"))
        path.replace(bad)
        return bad
    except OSError as exc:
        log.warning("could not set aside %s: %s", path, exc)
        return None


def load(path: str | os.PathLike, default: Any, validate: Validator | None = None,
         heal: bool = True) -> Loaded:
    """Read a JSON file, falling back to its .bak copy, then to `default`.

    Never raises for a missing or corrupt file: that is reported in the result.
    After a recovery the recovered content is written back to the main file
    (heal=True), so the app is not left depending on the backup alone.
    """
    path = Path(path)
    notes: list[str] = []
    if not path.exists():
        if not _bak(path).exists():
            return Loaded(default, "missing")        # genuinely the first run
        log.warning("%s is gone; trying its backup", path.name)
        notes.append(f"{path.name} was missing.")
    else:
        try:
            return Loaded(_read_one(path, validate), "file")
        except (OSError, ValueError) as exc:
            log.warning("%s is unreadable (%s)", path.name, exc)
            notes.append(f"{path.name} was unreadable ({exc}).")
        bad = _set_aside(path)
        if bad:
            notes.append(f"It was set aside as {bad.name}.")
    backup = _bak(path)
    if backup.exists():
        try:
            data = _read_one(backup, validate)
        except (OSError, ValueError) as exc:
            log.warning("backup %s is unreadable too (%s)", backup.name, exc)
            notes.append(f"The backup {backup.name} was unreadable too.")
            bad_backup = _set_aside(backup)
            if bad_backup:
                notes.append(f"It was set aside as {bad_backup.name}.")
        else:
            log.info("recovered %s from %s", path.name, backup.name)
            notes.append(f"Recovered from {backup.name}.")
            if heal:
                try:
                    save(path, data, validate, keep_backup=False)
                except (OSError, JsonFileError, fsguard.FsTimeout) as exc:
                    log.warning("could not rewrite %s after recovery: %s", path.name, exc)
                    notes.append(f"{path.name} could not be rewritten; the backup still holds it.")
                else:
                    notes.append(f"{path.name} has been restored from it.")
            return Loaded(data, "backup", notes)
    else:
        notes.append("No backup copy was available.")
    log.warning("starting %s from scratch", path.name)
    notes.append("Starting from scratch.")
    if heal:
        try:
            save(path, default, validate, keep_backup=False)
        except (OSError, JsonFileError, fsguard.FsTimeout) as exc:
            log.warning("could not write a fresh %s: %s", path.name, exc)
            notes.append(f"A fresh {path.name} could not be written.")
        else:
            notes.append(f"A fresh {path.name} has been written.")
    return Loaded(default, "missing", notes)


# On Windows the rename can be refused for a moment just after the temporary file is written:
# an antivirus scanner, the search indexer or a sync client opens a fresh file to look at it,
# and os.replace gets "Access is denied" (WinError 5) or "in use" (WinError 32) until it lets
# go. It clears in milliseconds, so the rename is tried again a few times before giving up -
# about half a second in all, well inside fsguard's limit. Seen once on Noah's machine in
# test_recent (2026-09-24), which writes projects.json fifteen times in a row.
REPLACE_RETRY_DELAYS_S = (0.02, 0.04, 0.08, 0.16, 0.3)


def _replace(tmp: Path, path: Path) -> None:
    """os.replace, tried again briefly when Windows says the file is in someone's hands."""
    for delay in REPLACE_RETRY_DELAYS_S:
        try:
            os.replace(tmp, path)          # atomic within one volume
            return
        except PermissionError as exc:
            log.info("rename of %s refused (%s); trying again in %.0f ms", path.name,
                     exc.strerror or exc, delay * 1000)
            time.sleep(delay)
    os.replace(tmp, path)                  # the last try raises for real


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())          # on disk before the rename
    _replace(tmp, path)


def save(path: str | os.PathLike, data: Any, validate: Validator | None = None,
         keep_backup: bool = True, timeout: float | None = None) -> None:
    """Write `data` as JSON, atomically, refreshing the .bak copy.

    The JSON text is produced (and validated) before anything is written.
    Raises JsonFileError if the data cannot be turned into JSON, or OSError /
    FsTimeout if the disk write fails.
    """
    path = Path(path)
    if validate:
        data = validate(data)
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    except (TypeError, ValueError) as exc:      # never reached from our own models
        raise JsonFileError(f"cannot save {path.name}: {exc}") from exc

    def _do() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = None
        if keep_backup and path.exists():
            try:
                previous = path.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("could not read %s for its backup (%s)", path.name, exc)
        _write_text(path, text)
        if keep_backup:
            # The backup holds the last known good content: the previous file when there
            # was one, otherwise this first save — so no file is ever left unprotected.
            try:
                _write_text(_bak(path), previous if previous is not None else text)
            except OSError as exc:              # a stale backup is not worth failing over
                log.warning("could not refresh %s (%s)", _bak(path).name, exc)

    fsguard.guarded(_do, timeout=timeout, what=f"saving {path}")
