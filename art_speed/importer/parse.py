"""The Datalens speed CSV, read strictly (plan section 5, SP-2).

File name: <sitenumber>_cam-<N>_speed.csv - an 8 or 9 digit QC Hub sitenumber and a cam number.
Since 0.1.2 also <sitenumber>_cam-<N>_speed_<gate version>.csv: Datalens now appends the gate
version (17790401_cam-1_speed_speed-ns-edit.csv). Only the name changed; the gate version is
not read for anything - it stays visible because the file name is stored and shown.

Two forms are accepted, and the form is recorded per file:

**raw** - as Datalens writes it:

    Sitecode:,17408304
    Start date:,2026-02-24
    Start time:,06:00 AM
    Duration (hrs):,13.00
    <blank>
    Date,Time,Direction,Class,Speed (In MPH),Track ID
    2026-02-24,06:00:12 AM,NB Thru,cl2_passenger_car,39,5

**post-macro** - the same file after the QA template's import macro stamped the cam number into
a seventh column and Excel saved it back: every line padded to seven columns, dates as m/d/yyyy,
times without the leading zero (1:19:52 AM), the header's duration possibly 0, and the cam
number at the end of every data row. A post-macro file's header duration can't be trusted, so
the header-vs-QC-Hub duration check is skipped for it (an advisory says so).

A header duration of 0 is accepted in both forms (Datalens writes 0.00 on a multiday export): the
count window's length then comes from QC Hub or, failing that, from the rows' own timestamps.

Anything else is a parser mismatch and the file is refused, named, with the line that did not
fit: a missing or reordered header line, a different column row, a row with the wrong number of
fields, a date or time that is not one, a speed that is not a whole number >= 0, a direction that
is not NB/SB/EB/WB, a movement that is not Thru (Datalens speed exports hold Thru only), or a
cam column that disagrees with the file name.

Exact duplicate data lines are kept and counted (an advisory, never dropped).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

RAW, POST_MACRO = "raw", "post-macro"
FILE_NAME = re.compile(r"^(\d{8,9})_cam-(\d+)_speed(?:_([^\\/]+))?\.csv$", re.IGNORECASE)
NAME_RULE = "<sitenumber>_cam-<N>_speed.csv or <sitenumber>_cam-<N>_speed_<gate version>.csv"
HEADER_LABELS = ("Sitecode:", "Start date:", "Start time:", "Duration (hrs):")
COLUMNS = ("Date", "Time", "Direction", "Class", "Speed (In MPH)", "Track ID")
DIRECTIONS = ("NB", "SB", "EB", "WB")

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_TIME_12 = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])$")
_DIRECTION = re.compile(r"^(NB|SB|EB|WB) (\S.*)$")


class ParseError(Exception):
    """The file is not a Datalens speed export this parser knows. Fatal for the file."""

    def __init__(self, file_name: str, message: str, line: int | None = None):
        self.file_name, self.line = file_name, line
        where = f"{file_name}, line {line}: " if line else f"{file_name}: "
        super().__init__(where + message)


@dataclass
class ParsedFile:
    path: Path
    file_name: str
    name_sitenumber: int                 # from the file name
    cam: int                             # from the file name
    form: str                            # RAW | POST_MACRO
    header_sitecode: str | None          # the 'Sitecode:' line, as text
    header_start_date: str | None        # 'YYYY-MM-DD'
    header_start_time: str | None        # 'HH:MM:SS'
    header_duration_hr: float | None
    file_hash: str
    rows: list = field(default_factory=list)   # (line_no, date, time, direction, movement, class, speed, track_id, extra)
    duplicate_lines: int = 0

    @property
    def header_sitenumber(self) -> int | None:
        text = (self.header_sitecode or "").strip()
        return int(text) if re.fullmatch(r"\d{8,9}", text) else None

    @property
    def sitenumbers_disagree(self) -> bool:
        """SP-Q11: the header's sitenumber is not the file name's (the pop-up case)."""
        return self.header_sitenumber != self.name_sitenumber

    @property
    def events(self) -> int:
        return len(self.rows)

    @property
    def zero_events(self) -> int:
        return sum(1 for r in self.rows if r[6] == 0)

    @property
    def dead(self) -> bool:
        """SP-Q9: every speed in the file is zero."""
        return self.events > 0 and self.zero_events == self.events


def match_name(file_name: str) -> tuple[int, int] | None:
    """(sitenumber, cam) from '<sitenumber>_cam-<N>_speed.csv' (with or without a gate version
    after _speed), or None when it isn't one."""
    m = FILE_NAME.match(file_name)
    if not m or int(m[2]) < 1:
        return None
    return int(m[1]), int(m[2])


def _as_date(text: str, allow_us: bool) -> str | None:
    text = text.strip()
    m = _ISO_DATE.match(text)
    try:
        if m:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        m = _US_DATE.match(text) if allow_us else None
        if m:
            return date(int(m[3]), int(m[1]), int(m[2])).isoformat()
    except ValueError:
        return None
    return None


def _as_time(text: str) -> str | None:
    """'06:00:12 AM' / '1:19:52 PM' / '06:00 AM' -> 'HH:MM:SS'."""
    m = _TIME_12.match(text.strip())
    if not m:
        return None
    h, mi, s = int(m[1]), int(m[2]), int(m[3] or 0)
    if not (1 <= h <= 12 and mi < 60 and s < 60):
        return None
    h = h % 12 + (12 if m[4].upper() == "PM" else 0)
    return f"{h:02d}:{mi:02d}:{s:02d}"


def _trim(fields: list[str]) -> list[str]:
    """Drop trailing empty fields (Excel pads every line to the widest row)."""
    out = list(fields)
    while out and not out[-1].strip():
        out.pop()
    return out


def parse(path: str | Path) -> ParsedFile:
    """Read one file. Raises ParseError (fatal for the file) with the file name and line."""
    path = Path(path)
    name = path.name
    matched = match_name(name)
    if matched is None:
        raise ParseError(name, f"the file name is not {NAME_RULE}")
    sitenumber, cam = matched
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ParseError(name, f"could not be read ({exc})") from exc
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError(name, "is not a text file (not UTF-8)") from exc
    lines = list(csv.reader(io.StringIO(text, newline="")))
    if len(lines) < 6:
        raise ParseError(name, "has no header block (fewer than six lines)")

    # ---- the four header lines, a blank line, the column row
    values = []
    for i, label in enumerate(HEADER_LABELS):
        fields = _trim(lines[i])
        if not fields or fields[0].strip() != label or len(fields) > 2:
            raise ParseError(name, f"expected '{label}' here", i + 1)
        values.append(fields[1].strip() if len(fields) == 2 else "")
    if _trim(lines[4]):
        raise ParseError(name, "expected a blank line after the header block", 5)
    column_row = _trim(lines[5])
    if tuple(c.strip() for c in column_row[:6]) != COLUMNS or len(column_row) > 7:
        raise ParseError(name, "the column row is not Date,Time,Direction,Class,Speed (In MPH),"
                               "Track ID", 6)

    # ---- the form: a seventh, filled column on the data rows means the macro has been through
    body = [(i + 1, row) for i, row in enumerate(lines) if i >= 6]
    body = [(n, row) for n, row in body if _trim(row)]            # blank lines between rows are skipped
    widths = {len(_trim(row)) for _, row in body}
    if widths - {6, 7}:
        n, row = next((n, row) for n, row in body if len(_trim(row)) not in (6, 7))
        raise ParseError(name, f"{len(_trim(row))} fields where 6 are expected", n)
    form = POST_MACRO if 7 in widths else RAW
    if form == POST_MACRO and 6 in widths:
        n = next(n for n, row in body if len(_trim(row)) == 6)
        raise ParseError(name, "some rows carry a cam column and this one does not", n)

    sitecode, start_date, start_time, duration = values
    header_date = _as_date(start_date, allow_us=True) if start_date else None
    header_time = _as_time(start_time) if start_time else None
    if form == RAW:
        if not re.fullmatch(r"\d{8,9}", sitecode):
            raise ParseError(name, f"'Sitecode:' is {sitecode!r}, not an 8 or 9 digit sitenumber", 1)
        if _as_date(start_date, allow_us=False) is None:
            raise ParseError(name, f"'Start date:' is {start_date!r}, not YYYY-MM-DD", 2)
        if header_time is None:
            raise ParseError(name, f"'Start time:' is {start_time!r}, not hh:mm AM/PM", 3)
    try:
        header_duration = float(duration) if duration else None
    except ValueError:
        if form == RAW:
            raise ParseError(name, f"'Duration (hrs):' is {duration!r}, not a number", 4) from None
        header_duration = None
    if form == RAW and (header_duration is None or header_duration < 0):
        raise ParseError(name, f"'Duration (hrs):' is {duration!r}, not a number", 4)

    # ---- the rows
    rows, seen, duplicates = [], set(), 0
    for line_no, raw in body:
        fields = [f.strip() for f in _trim(raw)]
        day = _as_date(fields[0], allow_us=form == POST_MACRO)
        if day is None:
            raise ParseError(name, f"{fields[0]!r} is not a date", line_no)
        clock = _as_time(fields[1])
        if clock is None:
            raise ParseError(name, f"{fields[1]!r} is not a time (hh:mm:ss AM/PM)", line_no)
        m = _DIRECTION.match(fields[2])
        if not m:
            raise ParseError(name, f"{fields[2]!r} is not a direction (NB/SB/EB/WB Thru)", line_no)
        if m[2] != "Thru":
            raise ParseError(name, f"{fields[2]!r}: speed exports hold Thru movements only", line_no)
        if not re.fullmatch(r"\d+", fields[4]):
            raise ParseError(name, f"speed {fields[4]!r} is not a whole number", line_no)
        if form == POST_MACRO:
            if not re.fullmatch(r"\d+", fields[6]) or int(fields[6]) != cam:
                raise ParseError(name, f"the cam column says {fields[6]!r} but the file is cam-{cam}",
                                 line_no)
        key = tuple(fields[:6])
        if key in seen:
            duplicates += 1
        seen.add(key)
        rows.append((line_no, day, clock, m[1], m[2], fields[3] or None, int(fields[4]),
                     fields[5] or None, None))

    return ParsedFile(path=path, file_name=name, name_sitenumber=sitenumber, cam=cam, form=form,
                      header_sitecode=sitecode or None, header_start_date=header_date,
                      header_start_time=header_time, header_duration_hr=header_duration,
                      file_hash=hashlib.sha256(data).hexdigest(), rows=rows,
                      duplicate_lines=duplicates)
