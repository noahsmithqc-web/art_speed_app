"""Reading a folder of speed CSVs before anything is written (SP-2).

    found = scan.scan_folder(folder, choices={})

Every file named <sitenumber>_cam-<N>_speed.csv is parsed; other files are listed and skipped.
The result says, without touching the database:

    files          every matching file: parsed, or refused with the reason (fatal for the file)
    unmatched      file names that are not speed exports
    disagreements  files whose header sitenumber is not the file name's and that have no choice
                   yet - the import stops and asks (SP-Q11's pop-up)
    sites          the parsed files grouped by the sitenumber they will be stored under
    fatal          batch-level refusals: the same sitenumber + cam twice (nothing is imported)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from art_speed.importer import parse

USE_NAME, USE_HEADER = "file name", "header"


@dataclass
class ScannedFile:
    file_name: str
    parsed: parse.ParsedFile | None = None
    error: str | None = None                 # fatal for the file
    sitenumber: int | None = None            # the one it will be stored under
    sitenumber_from: str = USE_NAME

    @property
    def ok(self) -> bool:
        return self.parsed is not None


@dataclass
class ScannedSite:
    sitenumber: int
    files: list = field(default_factory=list)        # ScannedFile, by cam

    @property
    def cams(self) -> list[int]:
        return [f.parsed.cam for f in self.files]

    @property
    def events(self) -> int:
        return sum(f.parsed.events for f in self.files)

    @property
    def first(self) -> parse.ParsedFile:
        return self.files[0].parsed

    def header_differences(self) -> list[str]:
        """Cam files of one sitenumber whose headers do not say the same thing (advisory)."""
        a, out = self.first, []
        for f in self.files[1:]:
            b = f.parsed
            if (a.header_start_date, a.header_start_time, a.header_duration_hr) != (
                    b.header_start_date, b.header_start_time, b.header_duration_hr):
                out.append(f"{b.file_name} has a different header (start / duration) from "
                           f"{a.file_name}; cam {a.cam}'s is used")
        return out


@dataclass
class Scan:
    folder: Path
    files: list = field(default_factory=list)            # ScannedFile, every matching file
    unmatched: list = field(default_factory=list)        # names
    disagreements: list = field(default_factory=list)    # dicts for the pop-up
    sites: dict = field(default_factory=dict)            # sitenumber -> ScannedSite
    fatal: list = field(default_factory=list)            # batch-level sentences

    @property
    def refused(self) -> list:
        return [f for f in self.files if not f.ok]

    @property
    def needs_choice(self) -> bool:
        return bool(self.disagreements)


class FolderError(Exception):
    """The folder can't be read. The message is written for the user."""


def scan_folder(folder: str | os.PathLike, choices: dict | None = None) -> Scan:
    """Parse every speed CSV in the folder (not its subfolders).

    `choices` maps a file name to 'file name' or 'header' for files whose two sitenumbers
    disagree. A disagreeing file without a choice lands in `disagreements`.
    """
    folder = Path(str(folder).strip()).expanduser()
    if not folder.is_dir():
        raise FolderError(f"{folder} is not a folder.")
    choices = choices or {}
    out = Scan(folder)
    try:
        names = sorted(p.name for p in folder.iterdir() if p.is_file())
    except OSError as exc:
        raise FolderError(f"{folder} could not be read ({exc}).") from exc

    for name in names:
        if parse.match_name(name) is None:
            out.unmatched.append(name)
            continue
        item = ScannedFile(name)
        out.files.append(item)
        try:
            item.parsed = parse.parse(folder / name)
        except parse.ParseError as exc:
            item.error = str(exc)
            continue
        p = item.parsed
        item.sitenumber = p.name_sitenumber
        if p.sitenumbers_disagree:
            choice = choices.get(name)
            if choice == USE_HEADER and p.header_sitenumber is not None:
                item.sitenumber, item.sitenumber_from = p.header_sitenumber, USE_HEADER
            elif choice == USE_NAME:
                item.sitenumber_from = USE_NAME
            else:
                out.disagreements.append({
                    "file": name, "file_name_sitenumber": p.name_sitenumber,
                    "header_sitecode": p.header_sitecode,
                    "header_usable": p.header_sitenumber is not None})

    by_key: dict = {}
    for item in out.files:
        if item.ok:
            by_key.setdefault((item.sitenumber, item.parsed.cam), []).append(item)
    for (sitenumber, cam), items in sorted(by_key.items()):
        if len(items) > 1:
            out.fatal.append(f"Sitenumber {sitenumber} cam {cam} appears {len(items)} times in this "
                             f"folder ({', '.join(i.file_name for i in items)}). Nothing was imported.")
        out.sites.setdefault(sitenumber, ScannedSite(sitenumber)).files.append(items[0])
    for site in out.sites.values():
        site.files.sort(key=lambda f: f.parsed.cam)
    return out
