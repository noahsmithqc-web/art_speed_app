"""Folder import: scan, look the sites up in QC Hub, write (plan section 5, SP-2).

    report = run.import_folder(project, folder, choices={...})

Outcomes (`report.status`):

    needs_choice  a file's name and header give different sitenumbers and no choice was passed:
                  nothing is written; the caller shows the pop-up and calls again with `choices`
    refused       a batch-level fatal (same sitenumber + cam twice), or no usable file: nothing written
    imported      one transaction: every sitenumber is stored, replacing what was there
    scanned       dry run: read and checked, nothing written

Re-importing a sitenumber is delete-and-replace (SP-Q8): its events, cams, corners, time masks,
QC Hub snapshot and lane edits go, and every decision of the locations built on it is reset.

Checks, by tier (the import report lists them per sitenumber; the drawer re-derives the lasting
ones live):

    fatal (file)    parser mismatch, non-Thru movement            -> the file is refused, named
    fatal (batch)   duplicate sitenumber + cam                    -> nothing is imported
    pop-up          file name vs header sitenumber                -> the user chooses
    warning         header vs QC Hub, off-leg events, dead cam
    advisory        identical lines, rows outside the count window, post-macro header not checked,
                    cam headers that differ, a near miss sibling already imported
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from art_speed import project as project_mod
from art_speed.importer import nm_hint, parse, scan
from art_speed.log import get_logger
from art_speed.qch import connection, site as qch_site
from art_speed.storage import db, store

log = get_logger(__name__)

NEEDS_CHOICE, REFUSED, IMPORTED, SCANNED = "needs_choice", "refused", "imported", "scanned"


@dataclass
class SiteResult:
    sitenumber: int
    cams: list
    files: list
    events: int
    overwritten: bool
    qch_lookup: str | None
    name: str
    location_id: int | None = None
    warnings: list = field(default_factory=list)
    advisories: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"sitenumber": str(self.sitenumber), "cams": self.cams, "files": self.files,
                "events": self.events, "overwritten": self.overwritten,
                "qch_lookup": self.qch_lookup, "name": self.name, "location_id": self.location_id,
                "warnings": self.warnings, "advisories": self.advisories}


@dataclass
class ImportReport:
    status: str
    folder: str
    sites: list = field(default_factory=list)            # SiteResult
    refused_files: list = field(default_factory=list)    # {'file', 'error'}
    unmatched: list = field(default_factory=list)
    disagreements: list = field(default_factory=list)
    fatal: list = field(default_factory=list)
    message: str = ""
    created_db: bool = False
    import_id: int | None = None

    def as_dict(self) -> dict:
        return {"status": self.status, "folder": self.folder, "message": self.message,
                "sites": [s.as_dict() for s in self.sites], "refused_files": self.refused_files,
                "unmatched": self.unmatched, "disagreements": self.disagreements,
                "fatal": self.fatal, "created_db": self.created_db, "import_id": self.import_id,
                "totals": {"sitenumbers": len(self.sites),
                           "files": sum(len(s.files) for s in self.sites),
                           "events": sum(s.events for s in self.sites),
                           "overwritten": sum(1 for s in self.sites if s.overwritten)}}


# ------------------------------------------------------------------ checks

def _window_bounds(day: str | None, clock: str | None, hours: float | None):
    if not (day and clock and hours):
        return None
    start = datetime.fromisoformat(f"{day}T{clock}")
    return start, start + timedelta(hours=hours)


def rows_outside(files: list, day: str | None, clock: str | None, hours: float | None) -> int:
    """Rows timestamped outside the count window. They are counted, never dropped (SP-Q11)."""
    bounds = _window_bounds(day, clock, hours)
    if bounds is None:
        return 0
    lo, hi = bounds[0].isoformat(sep=" "), bounds[1].isoformat(sep=" ")
    return sum(1 for f in files for r in f.rows if not (lo <= f"{r[1]} {r[2]}" < hi))


def site_checks(scanned: scan.ScannedSite, found: qch_site.Site, lanes: dict) -> tuple[list, list]:
    """(warnings, advisories) for one sitenumber, as sentences."""
    warnings, advisories = [], []
    files = [f.parsed for f in scanned.files]
    first = scanned.first

    # header vs QC Hub: the raw export's header has no location name, so the dates are compared
    if found.lookup == qch_site.LEGACYCOUNTS and found.count_date:
        if first.header_start_date and first.header_start_date != found.count_date:
            warnings.append(f"Header start date {first.header_start_date} is not QC Hub's count date "
                            f"{found.count_date}; the count window starts from QC Hub")
        if (first.header_start_time and found.start_time
                and first.header_start_time != found.start_time):
            warnings.append(f"Header start time {first.header_start_time[:5]} is not QC Hub's "
                            f"{found.start_time[:5]}; the count window starts from QC Hub")
        if first.form == parse.POST_MACRO:
            advisories.append("Post-macro file: its header duration was not checked against QC Hub")
        elif (first.header_duration_hr and found.duration_hr
              and abs(first.header_duration_hr - found.duration_hr) > 0.01):
            warnings.append(f"Header duration {first.header_duration_hr:g} h is not QC Hub's "
                            f"{found.duration_hr:g} h; the count window uses QC Hub")
    elif first.form == parse.POST_MACRO:
        advisories.append("Post-macro file: its header duration could not be checked")

    if not first.header_duration_hr and not (found.count_date and found.duration_hr):
        advisories.append("The file header gives a duration of 0: the count window's length was taken "
                          "from the rows' own timestamps (editable in Setup)")

    # the count window that will be stored: QC Hub's when it has one, else the header's
    if found.count_date and found.start_time and found.duration_hr:
        window = (found.count_date, found.start_time, found.duration_hr)
    else:
        window = (first.header_start_date, first.header_start_time, first.header_duration_hr or None)
    outside = rows_outside(files, *window)
    if outside:
        advisories.append(f"{outside:,} row{'s' if outside != 1 else ''} timestamped outside the "
                          "count window (kept and counted)")

    off: dict = {}
    for f in files:
        for r in f.rows:
            if not store.has_leg(lanes.get(r[3])):
                off[r[3]] = off.get(r[3], 0) + 1
    if off:
        warnings.append(", ".join(f"{n:,} {d} events" for d, n in sorted(off.items()))
                        + " in an approach with no lanes in the lane config (kept, never reported "
                          "unless the approach is ticked)")

    dead = [f.cam for f in files if f.dead]
    if dead:
        warnings.append(", ".join(f"Cam {c}" for c in dead) + " dead (100 % zero speeds)")

    for f in files:
        if f.duplicate_lines:
            advisories.append(f"{f.file_name}: {f.duplicate_lines:,} identical line"
                              f"{'s' if f.duplicate_lines != 1 else ''} (kept)")
        if not f.rows:
            advisories.append(f"{f.file_name}: no rows")
    advisories += scanned.header_differences()
    if found.lookup == qch_site.NOT_FOUND:
        warnings.append("Not in QC Hub: four approaches assumed; enter the location in Setup")
    elif found.lookup is None:
        warnings.append("Not checked against QC Hub: four approaches assumed")
    return warnings, advisories


# ------------------------------------------------------------------ the import

def import_folder(project: project_mod.Project, folder: str | os.PathLike, *,
                  choices: dict | None = None, qch: connection.Connection | None = None,
                  use_qch: bool = True, dry_run: bool = False) -> ImportReport:
    """Import every speed CSV in `folder` into the project's speed_######.db.

    `qch` is an open QC Hub connection (tests pass a fake); None opens one, and no login or no
    network leaves every site "not checked". `use_qch=False` skips QC Hub altogether.
    `dry_run` scans and checks and writes nothing.
    """
    try:
        found = scan.scan_folder(folder, choices)
    except scan.FolderError as exc:
        return ImportReport(REFUSED, str(folder), message=str(exc))
    report = ImportReport(IMPORTED, str(found.folder), unmatched=found.unmatched,
                          refused_files=[{"file": f.file_name, "error": f.error} for f in found.refused])
    if found.fatal:
        report.status, report.fatal = REFUSED, found.fatal
        report.message = found.fatal[0]
        return report
    if found.needs_choice:
        report.status, report.disagreements = NEEDS_CHOICE, found.disagreements
        report.message = ("The file name and the header give different sitenumbers for "
                          f"{len(found.disagreements)} file{'s' if len(found.disagreements) != 1 else ''}. "
                          "Choose which to use.")
        return report
    if not found.sites:
        report.status = REFUSED
        report.message = ("No file in this folder could be imported." if found.refused else
                          "No <sitenumber>_cam-<N>_speed.csv file in this folder.")
        return report

    numbers = sorted(found.sites)
    if use_qch:
        lookups = qch_site.lookup_sites(numbers, qch)
    else:
        lookups = {n: qch_site.Site(n, None, message="QC Hub lookup skipped") for n in numbers}
    hint = nm_hint.read(project.folder, project.number) if project.is_art_project else {}

    if dry_run:
        for n in numbers:
            s, q = found.sites[n], lookups[n]
            lanes = q.lane_configs if q.has_lane_configs else {d: db.MANUAL_LANE_CONFIG for d in db.DIRECTIONS}
            w, a = site_checks(s, q, lanes)
            report.sites.append(SiteResult(n, s.cams, [f.file_name for f in s.files], s.events,
                                           False, q.lookup, q.name, None, w, a))
        report.status, report.message = SCANNED, "Nothing was written."
        return report

    conn, report.created_db = db.open_or_create(project.folder, project.number)
    try:
        with db.transaction(conn):
            existing = set(store.sitenumbers(conn))
            import_id = store.insert_import(
                conn, str(found.folder), sitenumbers=len(numbers),
                files=sum(len(found.sites[n].files) for n in numbers),
                events=sum(found.sites[n].events for n in numbers),
                overwritten=sum(1 for n in numbers if n in existing))
            report.import_id = import_id
            results: dict = {}
            for n in numbers:
                s, q = found.sites[n], lookups[n]
                overwritten = n in existing
                if overwritten:
                    # slices of grouped locations that will survive: put back where they were
                    kept_slices = conn.execute(
                        "SELECT location_id, position, date, from_hour, to_hour FROM speed_slice "
                        "WHERE sitenumber = ? AND location_id IN (SELECT location_id FROM speed_slice "
                        "WHERE sitenumber <> ?)", (n, n)).fetchall()
                    gone = store.delete_sitenumber(conn, n)
                first = s.first
                store.insert_sitenumber(
                    conn, import_id, n, header_sitecode=first.header_sitecode,
                    header_start_date=first.header_start_date,
                    header_start_time=first.header_start_time,
                    header_duration_hr=first.header_duration_hr,
                    sitenumber_from=s.files[0].sitenumber_from, nm_location_code=hint.get(n))
                store.save_qch_site(conn, q)
                for f in s.files:
                    p = f.parsed
                    store.insert_cam(conn, n, p.cam, file_name=p.file_name, file_hash=p.file_hash,
                                     csv_form=p.form, events=p.events, zero_events=p.zero_events,
                                     duplicate_lines=p.duplicate_lines)
                    store.insert_events(conn, n, p.cam, p.rows)
                lanes, _ = store.lane_configs(conn, n)
                w, a = site_checks(s, q, lanes)
                results[n] = SiteResult(n, s.cams, [f.file_name for f in s.files], s.events,
                                        overwritten, q.lookup, q.name, None, w, a)
                store.log(conn, f"Imported {len(s.files)} file{'s' if len(s.files) != 1 else ''} "
                                f"(cam{'s' if len(s.cams) != 1 else ''} {', '.join(map(str, s.cams))}), "
                                f"{s.events:,} events, from {found.folder}", sitenumber=n)
                if overwritten:
                    store.log(conn, "Re-imported: the previous data, corners, time masks and lane "
                                    "edits were replaced and every decision of "
                                    f"{len(gone['locations_deleted']) + len(gone['locations_reset'])} "
                                    "location(s) built on this sitenumber was reset", sitenumber=n)
                for f in s.files:
                    if f.parsed.sitenumbers_disagree:
                        store.log(conn, f"{f.file_name}: sitenumber taken from the {f.sitenumber_from} "
                                        f"(file name says {f.parsed.name_sitenumber}, header says "
                                        f"{f.parsed.header_sitecode!r})", sitenumber=n)
                store.log(conn, f"QC Hub: {q.describe()}", sitenumber=n)
                # a grouped location that survived the overwrite gets its slices of this
                # sitenumber back, in the same places, with every decision reset
                if overwritten and gone["locations_reset"]:
                    for location_id, position, day, from_hour, to_hour in kept_slices:
                        conn.execute("INSERT INTO speed_slice (location_id, position, sitenumber, date, "
                                     "from_hour, to_hour) VALUES (?, ?, ?, ?, ?, ?)",
                                     (location_id, position, n, day, from_hour, to_hour))
                    for location_id in gone["locations_reset"]:
                        store.reset_location(conn, location_id)
                        store.log(conn, f"Sitenumber {n} was re-imported: every decision reset",
                                  location_id=location_id)
                        results[n].location_id = location_id

            # locations: sitenumbers of this batch that share a near miss location start grouped
            groups: dict = {}
            for n in numbers:
                if results[n].location_id is None:
                    code = hint.get(n)
                    groups.setdefault(("nm", code) if code else ("own", n), []).append(n)
            for (kind, code), members in groups.items():
                grouped = kind == "nm" and len(members) > 1
                location_id = store.create_location(
                    conn, [{"sitenumber": n} for n in members], nm_grouping_used=grouped)
                for n in members:
                    results[n].location_id = location_id
                if grouped:
                    store.log(conn, f"Sitenumbers {', '.join(map(str, members))} grouped into one "
                                    f"location: they share near miss location {code}",
                              location_id=location_id)
                elif kind == "nm":
                    siblings = [m for m, c in hint.items() if c == code and m != members[0]
                                and m in existing and m not in numbers]
                    if siblings:
                        results[members[0]].advisories.append(
                            f"Shares near miss location {code} with {', '.join(map(str, siblings))}, "
                            "already imported: group them from the table if one report is wanted")
            store.set_meta(conn, "last_import_utc", db.now_utc())
            report.sites = [results[n] for n in numbers]
    finally:
        conn.close()
    n_events = sum(s.events for s in report.sites)
    report.message = (f"Imported {len(report.sites)} sitenumber{'s' if len(report.sites) != 1 else ''}, "
                      f"{n_events:,} events.")
    unchecked = [lookups[n] for n in numbers if lookups[n].lookup is None]
    if unchecked and use_qch:
        report.message += f" QC Hub was not reached ({unchecked[0].message or 'no answer'})."
    log.info("speed import: %s from %s", report.message, found.folder)
    return report


def delete_sitenumber(project: project_mod.Project, sitenumber: int) -> dict:
    """Delete a sitenumber and its data (the table's kebab menu). Logged at project scope."""
    conn = db.open_db(project.folder, project.number)
    try:
        with db.transaction(conn):
            if not store.has_sitenumber(conn, sitenumber):
                raise KeyError(sitenumber)
            gone = store.delete_sitenumber(conn, sitenumber)
            store.log(conn, f"Sitenumber {sitenumber} deleted: {gone['events']:,} events, "
                            f"{gone['cams']} cam(s), {len(gone['locations_deleted'])} location(s)")
            for location_id in gone["locations_reset"]:
                store.log(conn, f"Sitenumber {sitenumber} was deleted from this location: every "
                                "decision reset", location_id=location_id)
        return gone
    finally:
        conn.close()
