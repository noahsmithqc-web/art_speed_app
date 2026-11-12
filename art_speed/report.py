"""The speed report workbook: a fill of the delivered template (plan section 7a, SP-5).

One workbook per location, written into a timestamped `reports_yyyy_mm_dd__hhmm` folder in the
project folder, named by sitenumber(s) and slice (SP-Q24):

    speed_17408304.xlsx                         one sitenumber
    speed_17408301_17408302.xlsx                grouped
    speed_17889013_2026-10-07.xlsx              split by day
    speed_17889013_2026-10-07_0600-1000.xlsx    split by time

What the tool writes (SP-Q27: "a template fill of the header info and the raw vehicle data"):

* Summary: the sitecode (E2), start time (E5) and duration (E6); "--" in the % over posted and
  posted cells of a direction that is not in the report (as the delivered workbooks have it);
  the report note under the table.
* Locations (the hidden helper sheet the header formulas look up): one row - sitenumber, name,
  count date, N/S and E/W posted speed, lat/long.
* NB / SB / EB / WB: the source cam's rows from row 8 - Date, Time, Direction, Class, Speed,
  Track ID and the helper hour - with zero speeds, speed-masked rows and time-masked hours left
  out (SP-Q25) and adjust-by applied, so the sheet's own PERCENTILE.INC sees exactly the rows the
  engine saw. A direction that is not in the report has no rows and its sheet is hidden.

Everything else is the template's: every formula, the hourly table, the five charts, the
threshold input (SP-Q28: the tool never computes the threshold), the logo, the styles. The
workbook is patched at the file level - only the sheets above, the shared-strings table and the
workbook's sheet list are rewritten; every chart, drawing and style part is copied byte for byte
(tests/test_report.py checks that).

The numbers are Excel's. After a run's workbooks are written, the tool has Excel open,
calculate and save each one (excel.py), so they are ready to send. Where Excel can't be run the
workbooks are still valid and marked to calculate when opened; the run's answer then says they
need opening and saving in Excel before they are sent.

The template holds one posted speed per axis (N/S and E/W). A location whose two directions on
an axis are both in the report with different posted speeds cannot be written into it correctly
(the sheet would measure both against the first), so generating it is refused with that reason.
"""

from __future__ import annotations

import os
import re
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from art_speed import config, contract, engine, excel, loader, payload
from art_speed.storage import db, store

DIRECTIONS = contract.DIRECTIONS
TEMPLATE = Path(__file__).resolve().parent / "reference" / "report_templates" / "speed_report_template.xlsx"
SHEETS = {"Summary": "xl/worksheets/sheet1.xml", "NB": "xl/worksheets/sheet2.xml",
          "SB": "xl/worksheets/sheet3.xml", "EB": "xl/worksheets/sheet4.xml",
          "WB": "xl/worksheets/sheet5.xml", "Locations": "xl/worksheets/sheet6.xml"}
STRINGS, WORKBOOK = "xl/sharedStrings.xml", "xl/workbook.xml"
CALC_CHAIN = "xl/calcChain.xml"
FIRST_DATA_ROW = 8
POSTED_COLUMNS = {"NB": "E", "SB": "F", "EB": "G", "WB": "H"}      # Summary rows 12 and 13
DATE_STYLE, TIME_STYLE = "1", "5"                                    # the template's date and h:mm:ss AM/PM cell styles
LOCATIONS_STYLE, LOCATIONS_DATE_STYLE = "6", "9"
NOTE_ROW = 16                                                        # under the Approach Summary, above the threshold chart


class ReportError(Exception):
    """The report can't be generated. The message is written for the user."""


@dataclass
class ReportData:
    """What goes into one workbook - nothing computed, only header facts and rows."""
    location_id: int
    file_name: str
    sitenumber: int
    name: str
    start_date: str | None
    start_time: str | None               # 'HH:MM:SS'
    duration_hours: float | None
    posted: dict                         # {direction: mph or None}
    included: list                       # directions in the report
    latitude: float | None
    longitude: float | None
    note: str
    rows: dict = field(default_factory=dict)     # {direction: [(date, time, 'NB Thru', class, speed, track_id)]}


# ------------------------------------------------------------------ names

def file_name(slices: list) -> str:
    """The workbook name of a location, from its slices (SP-Q24)."""
    sites = list(dict.fromkeys(str(s["sitenumber"]) for s in slices))
    name = "speed_" + "_".join(sites)
    first = slices[0]
    if len(sites) == 1:
        if first.get("date"):
            name += "_" + first["date"]
        if first.get("from_hour") is not None:
            name += f"_{first['from_hour']:02d}00-{first['to_hour']:02d}00"
    return name + ".xlsx"


def reports_folder(project_folder: str | os.PathLike, now: datetime | None = None) -> Path:
    """reports_yyyy_mm_dd__hhmm in the project folder; a second run in the same minute gets _2."""
    now = now or datetime.now()
    base = Path(project_folder) / now.strftime("reports_%Y_%m_%d__%H%M")
    folder, n = base, 1
    while folder.exists():
        n += 1
        folder = base.with_name(f"{base.name}_{n}")
    return folder


# ------------------------------------------------------------------ gathering

def gather(conn: sqlite3.Connection, location_id: int) -> ReportData:
    """Read what the workbook needs. Raises ReportError when the location can't be generated."""
    try:
        data = loader.load(conn, location_id)
    except KeyError:
        raise ReportError(f"Location {location_id} does not exist.") from None
    drawer = payload.build_drawer(data)
    code = drawer["location"]["code"]
    if drawer["status"]["state"] != contract.READY:
        raise ReportError(f"Location {code} is not Ready: " + "; ".join(drawer["status"]["missing"]) + ".")
    included = [d for d in DIRECTIONS if data.decisions[d]["include"]]
    posted = {d: data.decisions[d]["posted"] for d in DIRECTIONS}
    if not payload.location_name(data).strip():
        raise ReportError(f"Location {code} has no name (its sitenumber is not in QC Hub). Enter the "
                          "location in Setup, or type a manual name, then generate.")
    for axis, (a, b) in contract.AXES.items():
        if a in included and b in included and posted[a] != posted[b]:
            raise ReportError(
                f"Location {code}: {a} is posted {posted[a]:g} and {b} {posted[b]:g}. The report template "
                f"has one posted speed per axis ({'N/S' if axis == 'NS' else 'E/W'}), so its % over posted "
                f"for {b} would be measured against {a}'s. Not generated.")
    out = ReportData(
        location_id=location_id, file_name=file_name(data.slices), sitenumber=int(data.sites[0].sitenumber),
        name=payload.location_name(data), start_date=data.window.get("start_date"),
        start_time=data.window.get("start_time"), duration_hours=data.window.get("duration_hours"),
        posted=posted, included=included, latitude=data.sites[0].latitude, longitude=data.sites[0].longitude,
        note=data.note or "")
    groups = payload.cam_groups(data)                    # a grouped location's source is a cam group:
    for d in included:                                   # that cam number's file on every sitenumber
        dec = data.decisions[d]
        members = groups[drawer["directions"][d]["source_cam"]]
        masked = {h for cam in members for h in cam.masked_hours}
        low, high, adjust = dec["mask_low"], dec["mask_high"], dec["adjust_by"] or 0
        rows = []
        for cam in members:
            slices = [s for s in data.slices if str(s["sitenumber"]) == cam.sitenumber]
            for r in store.cam_rows(conn, int(cam.sitenumber), cam.cam, d):
                speed = r["speed_mph"]
                if speed == 0 or (low is not None and speed < low) or (high is not None and speed > high):
                    continue
                key = engine.hour_key(r["date"], r["time"])
                if key in masked or not engine.in_slices(slices, key):
                    continue
                rows.append((r["date"], r["time"], f"{r['direction']} {r['movement']}", r["class"] or "",
                             speed + adjust, r["track_id"] or ""))
        if len(members) > 1:
            rows.sort(key=lambda row: (row[0], row[1]))          # one run of rows, by date and time
        expected = drawer["directions"][d]["report"]["n"]
        if len(rows) != expected:                       # the sheet must hold exactly the engine's rows
            raise ReportError(f"Location {code} {d}: {len(rows)} rows to write but the engine counted "
                              f"{expected}. Not generated.")
        out.rows[d] = rows
    return out


# ------------------------------------------------------------------ writing the workbook

def _serial(iso: str) -> int:
    """Excel's day number of a date."""
    return (date.fromisoformat(iso) - date(1899, 12, 30)).days


def _fraction(hhmmss: str) -> str:
    """Excel's fraction of a day for a time."""
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return repr((h * 3600 + m * 60 + s) / 86400)


def _num(value) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


class _Strings:
    """New texts appended to the template's shared-strings table (existing indexes never move)."""

    def __init__(self, xml: str):
        self.xml = xml
        self.start = len(re.findall(r"<si>|<si/>", xml))
        self.index: dict = {}
        self.uses = 0

    def add(self, text: str) -> int:
        self.uses += 1
        if text not in self.index:
            self.index[text] = self.start + len(self.index)
        return self.index[text]

    def render(self) -> str:
        keep = ' xml:space="preserve"'                  # a text with leading or trailing spaces
        items = "".join(f"<si><t{keep if t != t.strip() else ''}>{escape(t)}</t></si>" for t in self.index)
        xml = self.xml.replace("</sst>", items + "</sst>")

        def bump(m):
            return f'{m.group(1)}="{int(m.group(2)) + (self.uses if m.group(1) == "count" else len(self.index))}"'
        head_end = xml.index(">", xml.index("<sst"))
        return re.sub(r'\b(count|uniqueCount)="(\d+)"', bump, xml[:head_end]) + xml[head_end:]


def _cell(ref: str, style: str | None, value=None, *, strings: _Strings | None = None, formula: str | None = None) -> str:
    s = f' s="{style}"' if style else ""
    if formula is not None:
        return f'<c r="{ref}"{s} t="str"><f>{escape(formula)}</f><v></v></c>'
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>'
    if isinstance(value, str):
        return f'<c r="{ref}"{s} t="s"><v>{strings.add(value)}</v></c>'
    return f'<c r="{ref}"{s}><v>{_num(value)}</v></c>'


def _replace_cell(xml: str, ref: str, make) -> str:
    """Swap one existing cell of a sheet. `make(style)` returns the new <c> element."""
    pattern = rf'<c r="{ref}"((?:\s[^>]*?)?)(?:/>|>.*?</c>)'
    def swap(m):
        style = re.search(r'\bs="(\d+)"', m.group(1))
        return make(style.group(1) if style else None)
    out, n = re.subn(pattern, swap, xml, count=1, flags=re.S)
    if n != 1:
        raise ReportError(f"The report template has no cell {ref}; it is not the template this tool fills.")
    return out


def _fill_summary(xml: str, data: ReportData, strings: _Strings) -> str:
    xml = _replace_cell(xml, "E2", lambda s: _cell("E2", s, data.sitenumber))
    xml = _replace_cell(xml, "E5", lambda s: _cell("E5", s, None) if not data.start_time
                        else f'<c r="E5" s="{s}"><v>{_fraction(data.start_time)}</v></c>')
    xml = _replace_cell(xml, "E6", lambda s: _cell("E6", s, data.duration_hours))
    for axis in contract.AXES.values():
        absent = [d for d in axis if d not in data.included]
        for d in absent:                                           # the direction's own % over posted
            ref = f"{POSTED_COLUMNS[d]}12"
            xml = _replace_cell(xml, ref, lambda s, ref=ref: _cell(ref, s, "--", strings=strings))
        # Posted speed row. The first direction's cell (NB, EB) is the axis's lookup and feeds the
        # threshold both directions are measured against, so it keeps its formula while either
        # direction is in the report; the second's (SB, WB) only mirrors it.
        blank = list(axis) if len(absent) == 2 else [d for d in absent if d == axis[1]]
        for d in blank:
            ref = f"{POSTED_COLUMNS[d]}13"
            xml = _replace_cell(xml, ref, lambda s, ref=ref: _cell(ref, s, "--", strings=strings))
    if data.note:
        row = (f'<row r="{NOTE_ROW}">' + _cell(f"C{NOTE_ROW}", None, "Note: " + data.note, strings=strings) + "</row>")
        marker = f'<row r="{NOTE_ROW + 2}"'
        if marker not in xml:
            raise ReportError("The report template's Summary sheet is not laid out as expected (row 18).")
        xml = xml.replace(marker, row + marker, 1)
    return xml


def _fill_locations(xml: str, data: ReportData, strings: _Strings) -> str:
    ns = next((data.posted[d] for d in ("NB", "SB") if d in data.included), None)
    ew = next((data.posted[d] for d in ("EB", "WB") if d in data.included), None)
    has_gps = data.latitude is not None and data.longitude is not None
    cells = [
        _cell("A2", LOCATIONS_STYLE, data.sitenumber),
        _cell("B2", LOCATIONS_STYLE, data.name or None, strings=strings),
        _cell("C2", LOCATIONS_DATE_STYLE, _serial(data.start_date) if data.start_date else None),
        _cell("D2", LOCATIONS_STYLE, ns), _cell("E2", LOCATIONS_STYLE, ew),
        _cell("F2", None, data.note or None, strings=strings),
        _cell("G2", LOCATIONS_STYLE, formula='I2&", "&J2') if has_gps else _cell("G2", LOCATIONS_STYLE),
        _cell("H2", LOCATIONS_STYLE),
        _cell("I2", LOCATIONS_STYLE, data.latitude), _cell("J2", LOCATIONS_STYLE, data.longitude)]
    out, n = re.subn(r'<row r="2"[^>]*>.*?</row>', lambda m: '<row r="2" spans="1:10">' + "".join(cells) + "</row>",
                     xml, count=1, flags=re.S)
    if n != 1:
        raise ReportError("The report template's Locations sheet has no row 2.")
    return out


def _fill_direction(xml: str, rows: list, strings: _Strings) -> str:
    if not rows:
        return xml
    parts = []
    for i, (day, clock, direction, klass, speed, track) in enumerate(rows):
        r = FIRST_DATA_ROW + i
        track_cell = (f'<c r="F{r}"><v>{int(track)}</v></c>' if re.fullmatch(r"\d{1,15}", track)
                      else _cell(f"F{r}", None, track or None, strings=strings))
        parts.append(
            f'<row r="{r}" spans="1:10"><c r="A{r}" s="{DATE_STYLE}"><v>{_serial(day)}</v></c>'
            f'<c r="B{r}" s="{TIME_STYLE}"><v>{_fraction(clock)}</v></c>'
            + _cell(f"C{r}", None, direction, strings=strings) + _cell(f"D{r}", None, klass or None, strings=strings)
            + f'<c r="E{r}"><v>{_num(speed)}</v></c>' + track_cell
            + _cell(f"G{r}", None, clock[:2], strings=strings) + "</row>")
    if xml.count("</sheetData>") != 1 or "</cols>" not in xml:
        raise ReportError("A direction sheet of the report template is not laid out as expected.")
    xml = xml.replace("</sheetData>", "".join(parts) + "</sheetData>", 1)
    xml = xml.replace("</cols>", '<col min="7" max="7" width="0" hidden="1" customWidth="1"/></cols>', 1)   # the helper hour column
    return re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:J{FIRST_DATA_ROW + len(rows) - 1}"/>', xml, count=1)


def _fill_workbook(xml: str, data: ReportData) -> str:
    hidden = [d for d in DIRECTIONS if d not in data.included] + ["Locations"]
    for name in hidden:
        out, n = re.subn(rf'<sheet name="{name}" sheetId="(\d+)" r:id=', rf'<sheet name="{name}" sheetId="\1" state="hidden" r:id=', xml, count=1)
        if n != 1:
            raise ReportError(f"The report template has no sheet {name}.")
        xml = out
    out, n = re.subn(r"<calcPr ([^>]*?)/>", r'<calcPr \1 fullCalcOnLoad="1"/>', xml, count=1)
    if n != 1:
        raise ReportError("The report template has no calculation settings to mark for recalculation.")
    return out


def write_workbook(data: ReportData, path: str | os.PathLike, template: str | os.PathLike = TEMPLATE) -> Path:
    """Fill a copy of the template with one location's header facts and rows."""
    path = Path(path)
    with zipfile.ZipFile(template) as z:
        infos = z.infolist()
        parts = {i.filename: z.read(i.filename) for i in infos}
    for needed in list(SHEETS.values()) + [STRINGS, WORKBOOK]:
        if needed not in parts:
            raise ReportError(f"The report template has no {needed}; it is not the template this tool fills.")
    text = lambda name: parts[name].decode("utf-8")
    strings = _Strings(text(STRINGS))
    new = {SHEETS["Summary"]: _fill_summary(text(SHEETS["Summary"]), data, strings),
           SHEETS["Locations"]: _fill_locations(text(SHEETS["Locations"]), data, strings),
           WORKBOOK: _fill_workbook(text(WORKBOOK), data)}
    for d in DIRECTIONS:
        new[SHEETS[d]] = _fill_direction(text(SHEETS[d]), data.rows.get(d, []), strings)
    new[STRINGS] = strings.render()
    # the stored calculation order no longer matches the cells: Excel rebuilds it on open
    if CALC_CHAIN in parts:
        new["[Content_Types].xml"] = re.sub(r'<Override PartName="/xl/calcChain\.xml"[^>]*/>', "",
                                            text("[Content_Types].xml"))
        new["xl/_rels/workbook.xml.rels"] = re.sub(r'<Relationship [^>]*Target="calcChain\.xml"[^>]*/>', "",
                                                   text("xl/_rels/workbook.xml.rels"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".writing")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for info in infos:
                if info.filename == CALC_CHAIN:
                    continue
                payload_bytes = new[info.filename].encode("utf-8") if info.filename in new else parts[info.filename]
                z.writestr(info.filename, payload_bytes, zipfile.ZIP_DEFLATED)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


# ------------------------------------------------------------------ a report run

def generate(project, location_ids: list | None = None, now: datetime | None = None,
             finalize=None) -> dict:
    """Generate workbooks for the given locations (None: every Ready location) into one new
    reports_ folder. All or nothing: if any location can't be generated, nothing is written.

    Returns {'folder': name, 'path': full path, 'files': [{'location_id', 'code', 'file'}],
             'excel': {'done': bool, 'message': str}}.
    `excel.done` says whether Excel calculated and saved the workbooks (ready to send); when it
    is False the message says what to do. Each workbook is logged in the location's paper trail
    and in speed_report_log. `finalize` defaults to excel.finalize; tests pass a stand-in.
    """
    finalize = finalize or excel.finalize
    conn = db.open_db(project.folder, project.number)
    try:
        if location_ids is None:
            location_ids = [i for i in store.location_ids(conn)
                            if loader.drawer(conn, i)["status"]["state"] == contract.READY]
            if not location_ids:
                raise ReportError("No location is Ready, so there is nothing to generate.")
        if not location_ids:
            raise ReportError("Tick at least one location to generate.")
        if len(set(location_ids)) != len(location_ids):
            raise ReportError("A location was listed twice.")
        problems, gathered = [], []
        for i in location_ids:
            try:
                gathered.append(gather(conn, i))
            except ReportError as exc:
                problems.append(str(exc))
        if problems:
            raise ReportError(" ".join(problems))
        names = [g.file_name for g in gathered]
        if len(set(names)) != len(names):               # cannot happen while a slice is in one location
            raise ReportError("Two locations would write the same workbook name.")
        folder = reports_folder(project.folder, now)
        written = []
        try:
            for g in gathered:
                write_workbook(g, folder / g.file_name)
                written.append(folder / g.file_name)
        except BaseException:
            for path in written:
                path.unlink(missing_ok=True)
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
            raise
        done, message = finalize(written)
        files = []
        with db.transaction(conn):
            for g in gathered:
                conn.execute(
                    "INSERT INTO speed_report_log (generated_utc, who, folder, file_name, location_id, "
                    "app_version) VALUES (?, ?, ?, ?, ?, ?)",
                    (db.now_utc(), store.user_name(), folder.name, g.file_name, g.location_id, config.VERSION))
                store.log(conn, f"Report generated: {folder.name}\\{g.file_name}"
                          + ("" if done else " (not calculated by Excel: open and save it before sending)"),
                          location_id=g.location_id)
                files.append({"location_id": g.location_id, "code": payload.location_code(
                    store.load_location(conn, g.location_id)["slices"]), "file": g.file_name})
        return {"folder": folder.name, "path": str(folder), "files": files,
                "excel": {"done": done, "message": message}}
    finally:
        conn.close()
