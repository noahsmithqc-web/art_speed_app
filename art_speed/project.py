"""Which project a folder is (plan 1.4, section 4).

The speed tool opens on a folder. If it is an A.R.T. project folder - a folder named with the
six-digit project number holding the project file ######.json - the number, name, state and
client come from that file. Any other folder works too: the project number is typed in and the
folder's name stands in for the project name.

The project file is only ever read here. The one place the speed tool writes a ######.json is
New project (create_project/wizard.py, A.R.T.'s wizard), which makes a new A.R.T. project folder;
an existing project file is never changed. nm_######.db and qch_######.db are never written.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from art_speed.storage import db


class ProjectError(Exception):
    """The folder can't be used as a speed project. The message is written for the user."""


@dataclass(frozen=True)
class Project:
    folder: Path
    number: str
    name: str | None = None
    state: str | None = None
    client: str | None = None
    is_art_project: bool = False

    @property
    def db_path(self) -> Path:
        return db.file_in(self.folder, self.number)

    def as_dict(self) -> dict:
        """The contract's `project` block."""
        return {"number": self.number, "name": self.name, "state": self.state,
                "client": self.client, "db": str(self.db_path), "is_art_project": self.is_art_project}


def _text(value) -> str | None:
    return (str(value).strip() or None) if value is not None else None


def art_project_file(folder: str | os.PathLike) -> Path:
    """The A.R.T. project file a ###### folder would hold, named after the folder."""
    folder = Path(folder)
    return folder / f"{folder.name}.json"


def open_folder(folder: str | os.PathLike, project_number: str | None = None) -> Project:
    """Recognise the folder. Raises ProjectError when it is neither an A.R.T. project folder
    nor given a six-digit project number."""
    folder = Path(str(folder).strip()).expanduser()
    if not folder.is_dir():
        raise ProjectError(f"{folder} is not a folder.")
    path = art_project_file(folder)
    if db.is_valid_project_number(folder.name) and path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ProjectError(f"{path.name} could not be read ({exc}).") from exc
        number = _text(raw.get("project_number")) if isinstance(raw, dict) else None
        if number != folder.name:
            raise ProjectError(f"{path.name} is for project {number!r}, not {folder.name}.")
        if project_number and project_number != number:
            raise ProjectError(f"{folder} is A.R.T. project {number}; it can't be opened as "
                               f"{project_number}.")
        return Project(folder, number, _text(raw.get("project_name")), _text(raw.get("state")),
                       _text(raw.get("client")), True)
    if not project_number:
        raise ProjectError(f"{folder} is not an A.R.T. project folder. Enter the six-digit "
                           "project number to use it.")
    if not db.is_valid_project_number(project_number):
        raise ProjectError(f"Project number {project_number!r} is not six digits.")
    return Project(folder, project_number, folder.name, None, None, False)
