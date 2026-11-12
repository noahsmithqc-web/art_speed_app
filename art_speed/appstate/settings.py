"""settings.json — app settings: the default project folder, and the QCH connection
minus its password (C4). The password itself only ever lives in Credential Manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from art_speed import fsguard, jsonfile, paths
from art_speed.log import get_logger

log = get_logger(__name__)

SETTINGS_VERSION = 1

# Status values from default_folder()
OK = "ok"              # a folder is saved and it is still there
MISSING = "missing"     # a folder is saved but no longer exists
UNSET = "unset"          # nothing saved yet


@dataclass
class Settings:
    default_project_folder: str | None = None
    # The non-secret half of the QCH login: host, port, user, database (C4). The password is
    # never here - it lives in Windows Credential Manager.
    qch: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"settings_version": SETTINGS_VERSION,
                "default_project_folder": self.default_project_folder,
                "qch": dict(self.qch)}


class FolderRejected(ValueError):
    """The folder cannot be used as the default project folder."""


class QchRejected(ValueError):
    """The QCH connection details are not usable."""


def _validate(raw: Any, dropped: list | None = None) -> dict:
    """Check the settings file. `dropped` collects the names of values removed on the way
    through, so load() can scrub the file instead of only ignoring them."""
    if not isinstance(raw, dict):
        raise ValueError("settings file is not a JSON object")
    folder = raw.get("default_project_folder")
    if folder is not None and not isinstance(folder, str):
        raise ValueError("default_project_folder must be text or empty")
    version = raw.get("settings_version", SETTINGS_VERSION)
    if not isinstance(version, int) or version < 1:
        raise ValueError("settings_version must be a whole number")
    if version > SETTINGS_VERSION:
        raise ValueError(f"settings file was written by a newer version of the app "
                         f"(settings_version {version})")
    qch = raw.get("qch") or {}
    if not isinstance(qch, dict):
        raise ValueError("qch must be a JSON object")
    clean_qch = {}
    for key in ("host", "user", "database"):
        value = qch.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"qch.{key} must be text or empty")
        if value:
            clean_qch[key] = value.strip()
    port = qch.get("port")
    if port not in (None, ""):
        try:
            clean_qch["port"] = int(port)
        except (TypeError, ValueError):
            raise ValueError("qch.port must be a whole number") from None
    if "password" in qch:
        # Never stored here, and never carried forward if someone put one in by hand.
        log.warning("a password in settings.json was ignored; it belongs in Credential Manager")
        if dropped is not None:
            dropped.append("qch.password")
    return {"settings_version": version, "default_project_folder": folder or None,
            "qch": clean_qch}


def load() -> jsonfile.Loaded:
    """Read settings. Returns the Loaded wrapper so callers can report a recovery.

    settings.json is a plain text file anyone can open, so a secret found in it is not just
    ignored - the file is rewritten without it, there and then. Reads do not normally write,
    but leaving a password on disk until some unrelated save happens is worse.
    """
    dropped: list = []
    result = jsonfile.load(paths.settings_file(), Settings().to_dict(),
                           lambda raw: _validate(raw, dropped))
    result.data = Settings(default_project_folder=result.data.get("default_project_folder"),
                           qch=result.data.get("qch") or {})
    if dropped:
        try:
            save(result.data)
        except (OSError, jsonfile.JsonFileError, fsguard.FsTimeout) as exc:
            log.error("could not remove %s from settings.json: %s", ", ".join(dropped), exc)
            result.notes.append(
                f"A password was found in settings.json and ignored, but it could not be "
                f"removed from the file ({exc}). Delete that line by hand.")
        else:
            log.warning("removed %s from settings.json", ", ".join(dropped))
            result.notes.append(
                "A password was found in settings.json. It has been removed - a password "
                "belongs in Windows Credential Manager, not in a file anyone can read.")
    return result


def save(settings: Settings) -> None:
    jsonfile.save(paths.settings_file(), settings.to_dict(), _validate)


def default_folder() -> tuple[str | None, str]:
    """(folder, status) for the wizard: OK, MISSING (saved but gone) or UNSET."""
    folder = load().data.default_project_folder
    if not folder:
        return None, UNSET
    if not Path(folder).is_absolute():
        # Only a hand-edited file or a pre-C6 save can hold one of these. It cannot be trusted:
        # it means a different folder depending on where the app was started, so it is reported
        # the same way a folder that has gone is, and the wizard offers Documents instead.
        log.warning("the saved default folder %r is a relative path; treating it as missing",
                    folder)
        return folder, MISSING
    try:
        there = fsguard.is_dir(folder)
    except OSError as exc:                      # includes a timed-out network drive
        log.warning("cannot check default folder %s: %s", folder, exc)
        there = False
    return folder, (OK if there else MISSING)


def check_folder(folder: str | Path) -> str:
    """Validate a candidate default folder. Returns the cleaned path, or raises FolderRejected."""
    if not str(folder).strip():
        raise FolderRejected("Choose a folder.")
    path = paths.full_path(folder)              # absolute before anything else looks at it
    try:
        paths.check_path_length(path)
    except paths.PathTooLong as exc:
        raise FolderRejected(str(exc)) from exc
    try:
        if not fsguard.is_dir(path):
            if fsguard.exists(path):
                raise FolderRejected(f"{path} is a file, not a folder.")
            raise FolderRejected(f"{path} is not a folder on this computer.")
        fsguard.check_writable(path)            # writes and deletes a small test file
    except fsguard.FsTimeout as exc:
        raise FolderRejected(f"{path} did not respond. If it is a network drive, "
                             "check that you are connected.") from exc
    except OSError as exc:
        raise FolderRejected(f"Cannot write to {path} ({exc.strerror or exc}).") from exc
    return str(path)


def set_default_folder(folder: str | Path) -> str:
    """Validate, then save. Returns the saved path."""
    try:
        cleaned = check_folder(folder)
    except FolderRejected as exc:
        log.info("default folder rejected: %s (%s)", folder, exc)
        raise
    current = load().data
    current.default_project_folder = cleaned
    save(current)
    log.info("default project folder set to %s", cleaned)
    return cleaned


def clear_default_folder() -> None:
    current = load().data
    current.default_project_folder = None
    save(current)
    log.info("default project folder cleared")


# ---------------------------------------------------------------- QCH connection (C4)

def qch_connection() -> dict:
    """The saved QCH host / port / user / database. Never holds the password."""
    return dict(load().data.qch)


def set_qch_connection(host: str, user: str, database: str, port: int | None = None) -> dict:
    """Save the non-secret half of the QCH login. The password goes to Credential Manager."""
    current = load().data
    saved = {"host": str(host).strip(), "user": str(user).strip(),
             "database": str(database).strip()}
    if not all(saved.values()):
        raise QchRejected("The QCH host, user and database are all needed.")
    if port:
        saved["port"] = int(port)
    current.qch = saved
    save(current)
    log.info("QCH connection saved: %s@%s/%s", saved["user"], saved["host"], saved["database"])
    return saved


def clear_qch_connection() -> None:
    current = load().data
    current.qch = {}
    save(current)
    log.info("QCH connection cleared")
