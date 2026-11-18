"""######_notes.json - the project's internal notes (0.1.1; A.R.T.'s project/notes.py, 1.2.13, copied).

The box at the bottom of the Speed tab (Summary in A.R.T.): free text for the team, with a little formatting and tick
boxes. It is never read by the report writers or an export; nothing outside Summary knows it
exists.

    {
      "notes_version": 1,
      "html": "<div><b>Delivery</b>: Fri 11/20</div><div><span class=\\"cb\\" ...></span> ...</div>",
      "saved_utc": "2026-11-17T20:35:00Z"
    }

It is its own file, not a field of ######.json, on purpose: the project file is rewritten by
every import and by Edit, and a copy of the app older than 1.2.13 writes back only the fields it
knows. Opening a project with an older build would have erased the notes without a word. A file
the older build never touches cannot be lost that way. It also keeps a tick in a to-do list
(saved on the spot) from rewriting the project file.

The text is stored as a small, fixed subset of HTML, and `clean` is the only way in: whatever
the page sends is rebuilt from that subset, so nothing that could run or reach outside the box
is ever written to disk or handed back to a browser.

    div, br                          lines
    b, strong, i, em, u              formatting
    span style="..."                 font-weight, font-style, text-decoration, color,
                                     background-color (plain colour values only)
    span class="cb" [data-on]        a tick box; data-on when ticked

Every line is its own div: text or formatting found outside one is wrapped, so a line is always
something the page can strike through when its box is ticked.

A missing file is an empty box. A file that cannot be read is set aside as .bad by jsonfile and
the box starts empty, so one damaged notes file never keeps a project from opening.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from art_speed import fsguard, jsonfile, paths
from art_speed.log import get_logger
from art_speed import manifest

log = get_logger(__name__)

NOTES_VERSION = 1
MAX_CHARS = 200_000          # about a hundred pages of text; a paste larger than this is a mistake

FORMAT_TAGS = ("b", "strong", "i", "em", "u")
BLOCK_TAGS = ("div",)
KEPT_TAGS = FORMAT_TAGS + BLOCK_TAGS + ("span",)
DROPPED_WITH_CONTENT = ("script", "style", "iframe", "object", "embed", "template", "noscript",
                        "head", "title", "svg", "math")

_COLOUR = re.compile(r"^(#[0-9a-f]{3,8}|rgba?\([0-9\s,.%/]+\)|transparent|[a-z]{3,20})$", re.I)
_STYLES = {
    "font-weight": re.compile(r"^(bold|normal|[1-9]00)$", re.I),
    "font-style": re.compile(r"^(italic|normal)$", re.I),
    "text-decoration": re.compile(r"^(underline|none)$", re.I),
    "text-decoration-line": re.compile(r"^(underline|none)$", re.I),
    "color": _COLOUR,
    "background-color": _COLOUR,
}


class NotesError(Exception):
    """The notes could not be saved. The message is for the user."""


@dataclass
class Notes:
    html: str = ""
    saved_utc: str = ""

    def to_dict(self) -> dict:
        return {"html": self.html, "saved_utc": self.saved_utc}


def file_name(project_number: str) -> str:
    return f"{project_number}_notes.json"


def file_in(project_folder: str | os.PathLike, project_number: str | None = None) -> Path:
    """The speed tool also opens folders that are not named with the project number, so the
    number is passed in; without one the folder's name is used, as in A.R.T."""
    folder = Path(project_folder)
    return folder / file_name(project_number or folder.name)


# ------------------------------------------------------------------ the subset

def _style(value: str) -> str:
    """The declarations of a style attribute that are in the subset, in the order given."""
    kept = []
    for part in value.split(";"):
        name, _, setting = part.partition(":")
        name, setting = name.strip().lower(), setting.strip()
        rule = _STYLES.get(name)
        if rule and rule.match(setting):
            kept.append(f"{name}:{setting}")
    return ";".join(kept)


class _Cleaner(HTMLParser):
    """Rebuilds the notes from the subset. Text always survives; a tag outside the subset is
    dropped and its text kept, except the few whose content is not text at all."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []        # the kept tags still open, innermost last
        self.skipping = 0                # depth inside a DROPPED_WITH_CONTENT tag
        self.wrapping = False            # inside a div added here around a bare line

    def _line(self) -> None:
        """Text or formatting is about to be written: outside any line, start one."""
        if not self.open and not self.wrapping:
            self.out.append("<div>")
            self.wrapping = True

    def _end_line(self) -> bool:
        """Close the div added around a bare line, if that is where we are."""
        if self.wrapping and not self.open:
            self.out.append("</div>")
            self.wrapping = False
            return True
        return False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in DROPPED_WITH_CONTENT:
            self.skipping += 1
            return
        if self.skipping:
            return
        if tag == "br":
            if self.open:
                self.out.append("<br>")
            elif not self._end_line():                   # a bare <br> is an empty line
                self.out.append("<div><br></div>")
            return
        if tag not in KEPT_TAGS:
            return
        if tag in BLOCK_TAGS:
            self._end_line()
        else:
            self._line()
        given = {name.lower(): (value or "") for name, value in attrs}
        text = f"<{tag}"
        if tag == "span":
            if "cb" in given.get("class", "").split():
                text += ' class="cb" contenteditable="false"'
                if "data-on" in given:
                    text += " data-on"
            else:
                style = _style(given.get("style", ""))
                if style:
                    text += f' style="{escape(style, quote=True)}"'
        self.out.append(text + ">")
        self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.handle_starttag(tag, attrs)
        if tag != "br":
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED_WITH_CONTENT:
            self.skipping = max(0, self.skipping - 1)
            return
        if self.skipping or tag not in self.open:
            return
        while self.open:                 # close anything left open inside it, then the tag
            last = self.open.pop()
            self.out.append(f"</{last}>")
            if last == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.skipping:
            return
        if not self.open and not self.wrapping and not data.strip():
            return                                       # white space between two lines
        self._line()
        self.out.append(escape(data, quote=False))

    def result(self) -> str:
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        self._end_line()
        return "".join(self.out)


def clean(html: Any) -> str:
    """The notes as they are stored: the subset and nothing else. Raises NotesError when what
    was sent is not text or is far too long."""
    if html is None:
        return ""
    if not isinstance(html, str):
        raise NotesError("The notes must be text.")
    if len(html) > MAX_CHARS:
        raise NotesError("The notes are too long to save. Shorten them and save again.")
    cleaner = _Cleaner()
    cleaner.feed(html)
    cleaner.close()
    text = cleaner.result()
    return "" if is_blank(text) else text


def is_blank(html: str) -> bool:
    """Nothing typed and no tick box: only empty lines."""
    if 'class="cb"' in html:
        return False
    return not re.sub(r"<[^>]*>", "", html).replace("&nbsp;", " ").replace(" ", " ").strip()


# ------------------------------------------------------------------ the file

def _validate(raw: Any) -> dict:
    """jsonfile hook, both ways: what is in the file is cleaned on the way in as well, so a
    hand-edited file cannot put anything on the page that Save would have refused."""
    if not isinstance(raw, dict):
        raise ValueError("the notes file is not a JSON object")
    version = raw.get("notes_version", NOTES_VERSION)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("notes_version must be a whole number")
    saved = raw.get("saved_utc") or ""
    if not isinstance(saved, str):
        raise ValueError("saved_utc must be text")
    try:
        html = clean(raw.get("html"))
    except NotesError as exc:
        raise ValueError(str(exc)) from exc
    return {"notes_version": NOTES_VERSION, "html": html, "saved_utc": saved}


def read(project_folder: str | os.PathLike, project_number: str | None = None) -> Notes:
    """The project's notes; an empty box when there is no file. Never raises: a slow drive or
    a damaged file gives an empty box and a line in the log, not a project that will not open."""
    path = file_in(project_folder, project_number)
    try:
        loaded = fsguard.guarded(jsonfile.load, path, None, _validate, False,
                                 what=f"reading {path}")
    except (fsguard.FsTimeout, OSError) as exc:
        log.warning("internal notes not read (%s)", exc)
        return Notes()
    for note in loaded.notes:
        log.warning("internal notes: %s", note)
    if not loaded.data:
        return Notes()
    return Notes(html=loaded.data["html"], saved_utc=loaded.data["saved_utc"])


def write(project_folder: str | os.PathLike, html: Any, project_number: str | None = None) -> Notes:
    """Save the notes (cleaned) and stamp them. Raises NotesError with a message for the user."""
    path = file_in(project_folder, project_number)
    notes = Notes(html=clean(html), saved_utc=manifest.now_utc())
    try:
        paths.check_path_length(path)
        jsonfile.save(path, {"notes_version": NOTES_VERSION, **notes.to_dict()}, _validate,
                      keep_backup=False)
    except paths.PathTooLong as exc:
        raise NotesError(str(exc)) from exc
    except (OSError, jsonfile.JsonFileError, fsguard.FsTimeout) as exc:
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        raise NotesError(f"Cannot save {path.name} ({reason}).") from exc
    return notes
