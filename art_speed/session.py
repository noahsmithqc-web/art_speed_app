"""Which project is open (SP-6). One at a time, as in A.R.T.

The page asks the engine what is open; this module is where the engine keeps the answer. Opening
a folder also puts it at the top of the recent projects list, so "projects are saved": the Home
screen lists them and one click reopens one. Adapted from A.R.T.'s project/session.py - the same
rules, over the speed tool's own idea of a project (project.Project: an A.R.T. project folder,
or any folder with a typed project number).
"""

from __future__ import annotations

import os

from art_speed import config, fsguard, paths, project as project_mod
from art_speed.appstate import recent
from art_speed.log import get_logger

log = get_logger(__name__)

_opened: project_mod.Project | None = None


class NoProjectOpen(Exception):
    def __init__(self, message: str = "No project is open. Open one from Home."):
        super().__init__(message)


class NeedsNumber(project_mod.ProjectError):
    """The folder is not an A.R.T. project folder, so the six-digit project number must be given."""


def peek() -> project_mod.Project | None:
    return _opened


def current() -> project_mod.Project:
    if _opened is None:
        raise NoProjectOpen()
    return _opened


def open_folder(folder: str | os.PathLike, project_number: str | None = None) -> project_mod.Project:
    """Open a project folder: a recent card, or "Open project folder". A refused open leaves
    whatever was already open alone. Raises NeedsNumber or project.ProjectError."""
    global _opened
    folder = paths.full_path(folder)
    number = (project_number or "").strip() or None
    try:
        opened = project_mod.open_folder(folder, number)
    except project_mod.ProjectError as exc:
        if number is None and "Enter the six-digit project number" in str(exc):
            raise NeedsNumber(str(exc)) from exc
        raise
    _opened = opened
    try:
        recent.touch(opened.number, opened.folder, opened.name)
    except Exception as exc:                 # the list is a convenience; never a reason not to open
        log.warning("recent projects list not updated (%s)", exc)
    log.info("open project: %s (%s)", opened.number, opened.folder)
    return opened


def still_there() -> str | None:
    """Check the open project's folder is still where it was. Returns why it was closed, or None
    when it is fine (or nothing is open). A drive that does not answer in time is left open."""
    global _opened
    if _opened is None:
        return None
    try:
        there = fsguard.is_dir(_opened.folder, timeout=config.OPEN_PROJECT_CHECK_S)
    except (fsguard.FsTimeout, OSError) as exc:
        log.warning("open project %s could not be checked (%s); left open", _opened.number, exc)
        return None
    if there:
        return None
    number, folder = _opened.number, _opened.folder
    log.warning("open project %s is no longer at %s; closing it", number, folder)
    _opened = None
    return f"Project {number} was closed because its folder is no longer at {folder}."


def close() -> None:
    """Back to Home with nothing open. Also what the tests use between runs."""
    global _opened
    if _opened is not None:
        log.info("closed project %s", _opened.number)
    _opened = None
