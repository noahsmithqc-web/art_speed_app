"""projects.json — the recent projects list behind the Home screen.

It holds only where each project is and when it was last opened, so the Home
screen can draw its cards without opening any project folder. Each project's own
######.json stays the source of truth; the pills are filled in afterwards from it
(C6). Entries are keyed by folder, so the same project number can appear twice
from two different folders.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from art_speed import config, jsonfile, paths
from art_speed.log import get_logger

log = get_logger(__name__)

RECENT_VERSION = 1


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_WINDOWS_PATH = re.compile(r"[A-Za-z]:|\\\\")
_UNC = re.compile(r"[\\/]{2}[^\\/]")


def tidy_folder(folder: str | os.PathLike) -> str:
    """The folder as it should be shown and stored: one separator style, no trailing one.

    On Windows - and for anything that looks like a Windows path anywhere - that means
    backslashes, so C:/ART/177690/ and C:\\ART\\177690 are stored the same way. A UNC path
    keeps its leading \\\\server\\share. Letter case is left as the user (or the folder
    picker) gave it.

    Elsewhere (the automated tests, Claude's sandbox) a POSIX path keeps its slashes, so a
    stored folder can still be opened on the machine it came from.
    """
    text = str(folder).strip()
    if not text:
        return text
    windows = paths.IS_WINDOWS or _WINDOWS_PATH.match(text) is not None or "\\" in text
    sep = "\\" if windows else "/"
    prefix = ""
    if windows and _UNC.match(text):            # \\server\share: the pair of separators stays
        prefix, text = sep * 2, text[2:]
    text = prefix + re.sub(r"[\\/]+", lambda _m: sep, text)
    trimmed = text[len(prefix):].rstrip(sep)
    if not trimmed:                              # the root on its own
        return prefix or sep
    if windows and re.fullmatch(r"[A-Za-z]:", trimmed):   # a drive keeps its separator
        return trimmed + sep
    return prefix + trimmed


def folder_key(folder: str | os.PathLike) -> str:
    """Comparison key for a folder.

    Windows treats C:\\ART\\177690, c:/art/177690 and C:\\ART\\177690\\ as the same
    folder, so the key collapses slashes, trailing separators and letter case.
    The rule is applied the same way on every platform, so the automated tests
    (which run on Linux) check the behaviour the app will actually have.
    """
    return tidy_folder(folder).lower()


@dataclass
class Entry:
    project_number: str
    project_name: str | None
    folder: str
    last_opened_utc: str

    def to_dict(self) -> dict:
        return {"project_number": self.project_number, "project_name": self.project_name,
                "folder": self.folder, "last_opened_utc": self.last_opened_utc}

    @classmethod
    def from_dict(cls, raw: dict) -> "Entry":
        return cls(project_number=raw["project_number"], project_name=raw.get("project_name"),
                   folder=raw["folder"], last_opened_utc=raw.get("last_opened_utc", ""))

    @property
    def key(self) -> str:
        return folder_key(self.folder)

    @property
    def card_id(self) -> str:
        """A short, URL-safe name for this entry (C6).

        The folder is still the identity - this is only a way to put it in a URL, since a
        Windows path in one would need escaping on both sides and a UNC path would need more
        than that. It is derived from the key, so it is the same every time the list is read
        and it changes if the project moves, which is what should happen.
        """
        return hashlib.sha1(self.key.encode("utf-8")).hexdigest()[:12]


def _validate(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("recent projects file is not a JSON object")
    version = raw.get("recent_version", RECENT_VERSION)
    if not isinstance(version, int) or version < 1:
        raise ValueError("recent_version must be a whole number")
    if version > RECENT_VERSION:
        raise ValueError(f"recent projects file was written by a newer version of the app "
                         f"(recent_version {version})")
    projects = raw.get("projects", [])
    if not isinstance(projects, list):
        raise ValueError("projects must be a list")
    clean: list[dict] = []
    seen: set[str] = set()
    for item in projects:
        if not isinstance(item, dict):
            raise ValueError("every entry in projects must be a JSON object")
        number = item.get("project_number")
        folder = item.get("folder")
        if not paths.is_valid_project_number(number if isinstance(number, str) else ""):
            raise ValueError(f"project_number {number!r} is not six digits")
        if not isinstance(folder, str) or not folder.strip():
            raise ValueError(f"entry {number} has no folder")
        name = item.get("project_name")
        if name is not None and not isinstance(name, str):
            raise ValueError(f"entry {number} has an invalid project_name")
        opened = item.get("last_opened_utc", "")
        if not isinstance(opened, str):
            raise ValueError(f"entry {number} has an invalid last_opened_utc")
        key = folder_key(folder)
        if key in seen:                        # keep the first (newest) of a duplicate
            log.warning("duplicate folder in recent projects: %s", folder)
            continue
        seen.add(key)
        clean.append({"project_number": number, "project_name": name or None,
                      "folder": folder, "last_opened_utc": opened})
    return {"recent_version": version, "projects": clean}


def _empty() -> dict:
    return {"recent_version": RECENT_VERSION, "projects": []}


def load() -> jsonfile.Loaded:
    """Read the list, newest first. Returns the Loaded wrapper (see jsonfile)."""
    result = jsonfile.load(paths.recent_file(), _empty(), _validate)
    entries = [Entry.from_dict(item) for item in result.data.get("projects", [])]
    entries.sort(key=lambda e: e.last_opened_utc, reverse=True)
    result.data = entries
    return result


def entries() -> list[Entry]:
    """The recent projects, newest first. Does not touch any project folder."""
    return load().data


def _save(items: list[Entry]) -> None:
    items = items[:config.RECENT_LIMIT]
    jsonfile.save(paths.recent_file(),
                  {"recent_version": RECENT_VERSION, "projects": [e.to_dict() for e in items]},
                  _validate)


def touch(project_number: str, folder: str | os.PathLike, project_name: str | None = None,
          when: str | None = None) -> Entry:
    """Add or update the entry for this folder and move it to the top of the list.

    Called when a project is created, opened, or its name is edited.
    """
    if not paths.is_valid_project_number(project_number):
        raise ValueError(f"project number {project_number!r} is not six digits")
    entry = Entry(project_number=project_number, project_name=project_name or None,
                  folder=tidy_folder(folder), last_opened_utc=when or now_utc())
    keep = [e for e in entries() if e.key != entry.key]
    _save([entry] + keep)
    log.info("recent projects: %s (%s)", project_number, folder)
    return entry


def remove(folder: str | os.PathLike) -> bool:
    """Drop the entry for this folder ("Remove from list"). True if one was removed."""
    key = folder_key(folder)
    current = entries()
    keep = [e for e in current if e.key != key]
    if len(keep) == len(current):
        return False
    _save(keep)
    log.info("recent projects: removed %s", folder)
    return True


def clear() -> None:
    _save([])
    log.info("recent projects: cleared")
