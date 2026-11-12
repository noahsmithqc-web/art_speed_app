"""######.json - the project file.

It is the single source of truth for a project: the Summary tab, the Home-screen pills and
every later ingest read it. Create Project writes it once; the Edit button changes three of
its fields; the import steps stamp `datasets`.

    {
      "manifest_version": 1,
      "project_number": "177690",
      "project_name": "Henrico Co 2026-05",
      "client": "Henrico County DPW",
      "state": "VA",
      "nm_region": "midatlantic_northeast",
      "qch_lookup": "found",
      "created_utc": "2026-09-16T17:05:00Z",
      "created_app_version": "0.1.0",
      "datasets": [{"type": "near miss", "last_updated_utc": "2026-09-18T15:12:40Z"}]
    }

Blank values are stored as null, never as "". nm_region is never entered: it is derived from
state every time the file is written, so the two can't drift apart.

There is no .bak and no repair here (Q12). A project file that can't be read, or whose number
doesn't match its folder, is refused with a clear message: the app never guesses at, or writes
into, a folder it didn't create.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from art_speed import config, fsguard, jsonfile, paths
from art_speed.log import get_logger
from art_speed.qch import regions

log = get_logger(__name__)

MANIFEST_VERSION = 1

# qch_lookup: what the lookup at creation found.
FOUND = "found"            # an order row existed
NOT_FOUND = "not_found"    # connected, but no order row
SKIPPED = "skipped"        # the lookup was not run (no connection, or --skip-qch)
LOOKUPS = (FOUND, NOT_FOUND, SKIPPED)


class ManifestError(Exception):
    """The project file is missing, unreadable, invalid, or for a different project."""


def now_utc() -> str:
    """The app's timestamp format: ISO 8601 UTC with a Z (the import logs use their own)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_name(project_number: str) -> str:
    return f"{project_number}.json"


def file_in(project_folder: str | os.PathLike) -> Path:
    """The project file inside a ###### folder, named after that folder."""
    folder = Path(project_folder)
    return folder / file_name(folder.name)


def _text(value: Any) -> str | None:
    """Text as stored: trimmed, and empty becomes null."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass
class Dataset:
    type: str
    last_updated_utc: str

    def to_dict(self) -> dict:
        return {"type": self.type, "last_updated_utc": self.last_updated_utc}


@dataclass
class Manifest:
    project_number: str
    project_name: str | None = None
    client: str | None = None
    state: str | None = None
    qch_lookup: str = SKIPPED
    created_utc: str = ""
    created_app_version: str = ""
    manifest_version: int = MANIFEST_VERSION
    datasets: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not paths.is_valid_project_number(self.project_number):
            raise ManifestError(f"Project number {self.project_number!r} is not six digits.")
        self.project_name = _text(self.project_name)
        self.client = _text(self.client)
        try:
            self.state = regions.clean_state(self.state)
        except regions.InvalidState as exc:
            raise ManifestError(str(exc)) from exc
        if self.qch_lookup not in LOOKUPS:
            raise ManifestError(f"qch_lookup {self.qch_lookup!r} must be one of "
                                f"{', '.join(LOOKUPS)}.")
        self.created_utc = self.created_utc or now_utc()
        self.created_app_version = self.created_app_version or config.VERSION

    # nm_region is derived, never stored on the object: state is the single input.
    @property
    def nm_region(self) -> str | None:
        return regions.region_for(self.state)

    def to_dict(self) -> dict:
        return {
            "manifest_version": self.manifest_version,
            "project_number": self.project_number,
            "project_name": self.project_name,
            "client": self.client,
            "state": self.state,
            "nm_region": self.nm_region,
            "qch_lookup": self.qch_lookup,
            "created_utc": self.created_utc,
            "created_app_version": self.created_app_version,
            "datasets": [d.to_dict() for d in self.datasets],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Manifest":
        clean = validate(raw)
        return cls(
            project_number=clean["project_number"],
            project_name=clean["project_name"],
            client=clean["client"],
            state=clean["state"],
            qch_lookup=clean["qch_lookup"],
            created_utc=clean["created_utc"],
            created_app_version=clean["created_app_version"],
            manifest_version=clean["manifest_version"],
            datasets=[Dataset(**d) for d in clean["datasets"]],
        )

    # ------------------------------------------------------------- datasets
    def dataset(self, dataset_type: str) -> Dataset | None:
        for item in self.datasets:
            if item.type == dataset_type:
                return item
        return None

    def stamp(self, dataset_type: str, when: str | None = None) -> Dataset:
        """Record that data of this type was added, overwritten or partly deleted.

        Only the latest update is kept; the history lives in each database's import log.
        """
        if dataset_type not in config.DATASET_TYPES:
            raise ManifestError(
                f"{dataset_type!r} is not one of the app's data types "
                f"({', '.join(config.DATASET_TYPES)}). Add it to config.DATASET_TYPES first.")
        stamp = when or now_utc()
        existing = self.dataset(dataset_type)
        if existing:
            existing.last_updated_utc = stamp
            return existing
        item = Dataset(type=dataset_type, last_updated_utc=stamp)
        self.datasets.append(item)
        return item

    def drop(self, dataset_type: str) -> bool:
        """Remove a type's entry: all of its data has been deleted. True if there was one."""
        keep = [d for d in self.datasets if d.type != dataset_type]
        if len(keep) == len(self.datasets):
            return False
        self.datasets = keep
        return True


def validate(raw: Any) -> dict:
    """Check a project file's contents. Raises ManifestError with a message for the user."""
    if not isinstance(raw, dict):
        raise ManifestError("The project file is not a JSON object.")

    version = raw.get("manifest_version", MANIFEST_VERSION)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ManifestError("manifest_version must be a whole number.")
    if version > MANIFEST_VERSION:
        raise ManifestError("This project was created by a newer version of the app "
                            f"(manifest_version {version}). Update A.R.T. to open it.")

    number = raw.get("project_number")
    if not paths.is_valid_project_number(number if isinstance(number, str) else ""):
        raise ManifestError(f"project_number {number!r} is not six digits.")

    values: dict = {"manifest_version": version, "project_number": number}
    for key in ("project_name", "client"):
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            raise ManifestError(f"{key} must be text or empty.")
        values[key] = _text(value)

    state = raw.get("state")
    if state is not None and not isinstance(state, str):
        raise ManifestError("state must be text or empty.")
    try:
        values["state"] = regions.clean_state(state)
    except regions.InvalidState as exc:
        raise ManifestError(str(exc)) from exc

    lookup = raw.get("qch_lookup", SKIPPED)
    if lookup not in LOOKUPS:
        raise ManifestError(f"qch_lookup {lookup!r} must be one of {', '.join(LOOKUPS)}.")
    values["qch_lookup"] = lookup

    for key in ("created_utc", "created_app_version"):
        value = raw.get(key, "")
        if value is not None and not isinstance(value, str):
            raise ManifestError(f"{key} must be text.")
        values[key] = value or ""

    raw_datasets = raw.get("datasets", [])
    if not isinstance(raw_datasets, list):
        raise ManifestError("datasets must be a list.")
    datasets: list = []
    seen: set = set()
    for item in raw_datasets:
        if not isinstance(item, dict):
            raise ManifestError("every entry in datasets must be a JSON object.")
        dtype = item.get("type")
        updated = item.get("last_updated_utc")
        if not isinstance(dtype, str) or not dtype.strip():
            raise ManifestError("every entry in datasets needs a type.")
        if updated is not None and not isinstance(updated, str):
            raise ManifestError(f"datasets entry {dtype!r} has an invalid last_updated_utc.")
        dtype = dtype.strip()
        if dtype in seen:
            raise ManifestError(f"datasets lists {dtype!r} twice.")
        seen.add(dtype)
        if dtype not in config.DATASET_TYPES:
            # Reading stays permissive so a file written by a newer slice still opens; only
            # the app's own stamp() refuses a type that isn't pinned in config.
            log.warning("project file lists an unknown data type %r", dtype)
        datasets.append({"type": dtype, "last_updated_utc": updated or ""})
    values["datasets"] = datasets

    # nm_region is recomputed from state on every write; whatever the file said is ignored.
    values["nm_region"] = regions.region_for(values["state"])
    return values


def _validate_for_save(raw: Any) -> dict:
    """jsonfile.save hook: validate, then return the dict in the order the file uses."""
    clean = validate(raw)
    return {key: clean[key] for key in
            ("manifest_version", "project_number", "project_name", "client", "state",
             "nm_region", "qch_lookup", "created_utc", "created_app_version", "datasets")}


def read(project_folder: str | os.PathLike, project_number: str | None = None) -> Manifest:
    """Read the project file in a ###### folder. Raises ManifestError with a clear message.

    The number in the file must match the folder it sits in (and `project_number` when the
    caller knows what it expects). Nothing is repaired: a bad file is refused (Q12).
    """
    folder = Path(project_folder)
    expected = project_number or folder.name
    path = file_in(folder)
    try:
        # utf-8-sig, not utf-8: Word and Notepad's "UTF-8 with BOM" would otherwise make a
        # hand-edited file unreadable for a reason nobody could see. We always write plain UTF-8.
        text = fsguard.guarded(path.read_text, encoding="utf-8-sig",
                               what=f"reading {path}")
    except fsguard.FsTimeout as exc:
        raise ManifestError(f"{folder} did not respond. If it is a network drive, check that "
                            "you are connected.") from exc
    except FileNotFoundError as exc:
        raise ManifestError(f"{folder} has no {file_name(folder.name)}, so it is not an "
                            "A.R.T. project folder.") from exc
    except OSError as exc:
        raise ManifestError(f"{path} could not be read ({exc.strerror or exc}).") from exc

    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise ManifestError(f"{path.name} is not valid JSON ({exc}).") from exc

    found = Manifest.from_dict(raw)
    if paths.is_valid_project_number(expected) and found.project_number != expected:
        raise ManifestError(f"{path.name} is for project {found.project_number}, not {expected}.")
    return found


def write(project_folder: str | os.PathLike, manifest: Manifest) -> Path:
    """Write the project file atomically (tmp -> flush -> rename). Raises OSError / FsTimeout."""
    path = file_in(Path(project_folder))
    paths.check_path_length(path)
    jsonfile.save(path, manifest.to_dict(), _validate_for_save, keep_backup=False)
    return path
