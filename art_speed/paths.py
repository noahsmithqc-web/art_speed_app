"""Where the app keeps things, and the rules for paths.

Nothing here creates files except ensure_app_home().
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from art_speed import config

IS_WINDOWS = sys.platform == "win32"
_PROJECT_NUMBER = re.compile(r"[0-9]{6}")


class PathTooLong(ValueError):
    """The full path would exceed config.MAX_PATH_CHARS."""

    def __init__(self, path: str | os.PathLike, limit: int = config.MAX_PATH_CHARS):
        self.path = str(path)
        self.length = len(self.path)
        self.limit = limit
        super().__init__(
            f"The full path is {self.length} characters; the limit is {limit}. "
            "Choose a folder with a shorter path."
        )


# ---------------------------------------------------------------- app-state folder

def app_home_source() -> str:
    """'SPEED_HOME' when the development override is set, otherwise 'default'."""
    return config.ENV_HOME if os.environ.get(config.ENV_HOME, "").strip() else "default"


def app_home() -> Path:
    """Folder for settings, the recent projects list and logs.

    %LOCALAPPDATA%\\SpeedTool on Windows, unless SPEED_HOME is set (development only).
    """
    override = os.environ.get(config.ENV_HOME, "").strip()
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / config.APP_DIR_NAME
    # Non-Windows (automated tests, Claude's sandbox): a conventional per-user location.
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / config.APP_DIR_NAME


def settings_file() -> Path:
    return app_home() / "settings.json"


def recent_file() -> Path:
    return app_home() / "projects.json"


def log_dir() -> Path:
    return app_home() / "logs"


def log_file() -> Path:
    return log_dir() / "speed.log"


def ensure_app_home() -> Path:
    """Create the app-state folder (and logs\\) if missing. Returns the folder."""
    home = app_home()
    log_dir().mkdir(parents=True, exist_ok=True)
    return home


# ---------------------------------------------------------------- Documents fallback

def _windows_documents() -> Path | None:
    """The real Documents folder, including OneDrive redirection (SHGetKnownFolderPath)."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        folder_id = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                         (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None,
                                                         ctypes.byref(out))
        try:
            return Path(out.value) if hr == 0 and out.value else None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except Exception:  # any failure -> caller falls back to ~\Documents
        return None


def documents_folder() -> Path:
    """Fallback save folder when no default project folder is set."""
    if IS_WINDOWS:
        found = _windows_documents()
        if found:
            return found
    return Path.home() / "Documents"


# ---------------------------------------------------------------- bundled files

def resource_path(*parts: str) -> Path:
    """A file shipped with the app (web pages, reference CSVs).

    Works both from source and inside the packaged app (C8), where PyInstaller
    unpacks files under sys._MEIPASS.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath("art_speed", *parts)


# ---------------------------------------------------------------- rules

def full_path(folder: str | os.PathLike) -> Path:
    """A folder as the app stores it: absolute, with ~ and any .. resolved.

    Every folder that reaches settings.json or projects.json goes through this. A relative path
    ("..", "ART_projects_dev") only means something from the folder the app happened to start
    in, so storing one hands a later run - or the packaged app, started from anywhere - a folder
    that is not the one the user meant. C3 learned this for the recent projects list; the rule
    lives here now so there is one copy of it.
    """
    return Path(str(folder).strip()).expanduser().absolute().resolve()


def is_valid_project_number(value: str) -> bool:
    """Exactly six ASCII digits (leading zeros allowed)."""
    return isinstance(value, str) and _PROJECT_NUMBER.fullmatch(value) is not None


def check_path_length(path: str | os.PathLike, limit: int = config.MAX_PATH_CHARS) -> None:
    """Raise PathTooLong if the full path is over the limit."""
    if len(str(path)) > limit:
        raise PathTooLong(path, limit)
