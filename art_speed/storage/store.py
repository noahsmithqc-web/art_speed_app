"""Reading and writing speed_######.db. Every statement that touches the tables is here.

Nothing in this module decides anything: the import (SP-2) decides what a file is worth, the
engine (SP-3) does the arithmetic, the edits (SP-4) validate a user's change. These functions
store and fetch, inside the caller's transaction (db.transaction).
"""

from __future__ import annotations

import getpass
import json
import sqlite3

from art_speed.qch import site as qch_site
from art_speed.storage import db

DIRECTIONS = db.DIRECTIONS
AXES = {"NS": ("NB", "SB"), "EW": ("EB", "WB")}


def user_name() -> str:
    """The Windows user name for the paper trail; 'user' when it can't be read."""
    try:
        return getpass.getuser() or "user"
    except Exception:                       # no USERNAME / no passwd entry
        return "user"


# ------------------------------------------------------------------ paper trail

def log(conn: sqlite3.Connection, what: str, *, location_id: int | None = None,
        sitenumber: int | None = None, cam: int | None = None, who: str | None = None,
        when: str | None = None) -> None:
    """One paper-trail line. The scope follows from what it is about:
    a cam (sitenumber + cam), a sitenumber, a location, or the project."""
    scope = ("cam" if cam is not None else "site" if sitenumber is not None
             else "location" if location_id is not None else "project")
    conn.execute(
        "INSERT INTO speed_log (logged_utc, who, scope, location_id, sitenumber, cam, what) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (when or db.now_utc(), who or user_name(), scope, location_id, sitenumber, cam, what))


def trail(conn: sqlite3.Connection, location_id: int) -> list[sqlite3.Row]:
    """A location's paper trail, newest first: its own lines plus those of its sitenumbers
    and their cams (which every location built on the sitenumber shares)."""
    return conn.execute(
        """SELECT log_id, logged_utc, who, scope, location_id, sitenumber, cam, what
           FROM speed_log
           WHERE (scope = 'location' AND location_id = ?)
              OR (scope IN ('site', 'cam') AND sitenumber IN
                  (SELECT sitenumber FROM speed_slice WHERE location_id = ?))
           ORDER BY logged_utc DESC, log_id DESC""", (location_id, location_id)).fetchall()


# ------------------------------------------------------------------ meta

def meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM speed_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO speed_meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))


# ------------------------------------------------------------------ imports and sitenumbers

def insert_import(conn: sqlite3.Connection, folder: str, *, sitenumbers: int, files: int,
                  events: int, overwritten: int = 0) -> int:
    from art_speed import config
    cur = conn.execute(
        "INSERT INTO speed_import_log (imported_utc, imported_by, folder, app_version, "
        "sitenumbers, files, events, overwritten) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (db.now_utc(), user_name(), folder, config.VERSION, sitenumbers, files, events, overwritten))
    return cur.lastrowid


def last_import(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM speed_import_log ORDER BY import_id DESC LIMIT 1").fetchone()


def sitenumbers(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT sitenumber FROM speed_sitenumber ORDER BY sitenumber")]


def has_sitenumber(conn: sqlite3.Connection, sitenumber: int) -> bool:
    return conn.execute("SELECT 1 FROM speed_sitenumber WHERE sitenumber = ?",
                        (sitenumber,)).fetchone() is not None


def sitenumber_row(conn: sqlite3.Connection, sitenumber: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM speed_sitenumber WHERE sitenumber = ?",
                        (sitenumber,)).fetchone()


def insert_sitenumber(conn: sqlite3.Connection, import_id: int, sitenumber: int, *,
                      header_sitecode: str | None = None, header_start_date: str | None = None,
                      header_start_time: str | None = None, header_duration_hr: float | None = None,
                      sitenumber_from: str = "file name", nm_location_code: str | None = None) -> None:
    conn.execute(
        "INSERT INTO speed_sitenumber (sitenumber, import_id, header_sitecode, header_start_date, "
        "header_start_time, header_duration_hr, sitenumber_from, nm_location_code) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sitenumber, import_id, header_sitecode, header_start_date, header_start_time,
         header_duration_hr, sitenumber_from, nm_location_code))


def insert_cam(conn: sqlite3.Connection, sitenumber: int, cam: int, *, file_name: str,
               file_hash: str, csv_form: str, events: int, zero_events: int,
               duplicate_lines: int = 0) -> None:
    conn.execute(
        "INSERT INTO speed_cam (sitenumber, cam, file_name, file_hash, csv_form, events, "
        "zero_events, duplicate_lines) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sitenumber, cam, file_name, file_hash, csv_form, events, zero_events, duplicate_lines))


def insert_events(conn: sqlite3.Connection, sitenumber: int, cam: int, rows) -> None:
    """rows: (line_no, date, time, direction, movement, class, speed_mph, track_id, extra)."""
    conn.executemany(
        "INSERT INTO speed_event (sitenumber, cam, line_no, date, time, direction, movement, "
        "class, speed_mph, track_id, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ((sitenumber, cam) + tuple(r) for r in rows))


def cams(conn: sqlite3.Connection, sitenumber: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM speed_cam WHERE sitenumber = ? ORDER BY cam",
                        (sitenumber,)).fetchall()


def cam_bins(conn: sqlite3.Connection, sitenumber: int, cam: int) -> dict:
    """{direction: {'YYYY-MM-DD HH': {speed: rows}}} - the cam's whole file, zeros included.

    Hourly 1-mph bin counts are exact for every statistic the tool prints (a percentile over
    bin counts equals the percentile over the rows), and far smaller than the rows themselves.
    """
    out: dict = {}
    for direction, day, hour, speed, n in conn.execute(
            "SELECT direction, date, substr(time, 1, 2), speed_mph, COUNT(*) FROM speed_event "
            "WHERE sitenumber = ? AND cam = ? GROUP BY direction, date, substr(time, 1, 2), speed_mph",
            (sitenumber, cam)):
        out.setdefault(direction, {}).setdefault(f"{day} {hour}", {})[speed] = n
    return out


def cam_rows(conn: sqlite3.Connection, sitenumber: int, cam: int,
             direction: str | None = None) -> list[sqlite3.Row]:
    """The raw rows of a cam in file order (optionally one direction): exports and the report."""
    sql = ("SELECT event_id, line_no, date, time, direction, movement, class, speed_mph, track_id, "
           "extra FROM speed_event WHERE sitenumber = ? AND cam = ?")
    params: tuple = (sitenumber, cam)
    if direction:
        sql += " AND direction = ?"
        params += (direction,)
    return conn.execute(sql + " ORDER BY date, time, line_no", params).fetchall()


# ------------------------------------------------------------------ the QC Hub snapshot

def save_qch_site(conn: sqlite3.Connection, site: qch_site.Site) -> None:
    """Store (or replace) what QC Hub said about a sitenumber.

    A site with no lane configs at all - not in QC Hub, not checked, a tube/survey site, or a
    turn movement site whose configs are blank - gets four approaches of one thru lane, so every
    direction exists until the user says otherwise (SP-Q5: "assume four directions").
    """
    configs = dict(site.lane_configs)
    if not any(configs.values()):
        configs = {d: db.MANUAL_LANE_CONFIG for d in DIRECTIONS}
    conn.execute("DELETE FROM speed_qch_site WHERE sitenumber = ?", (site.sitenumber,))
    conn.execute(
        "INSERT INTO speed_qch_site (sitenumber, lookup, street_ns, street_ew, location_name, city, "
        "state, nb_lane_config, sb_lane_config, eb_lane_config, wb_lane_config, control_type, "
        "stop_controlled, latitude, longitude, count_date, start_time, duration_hr, checked_utc, "
        "notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (site.sitenumber, site.lookup, site.street_ns, site.street_ew, site.location_name,
         site.city, site.state, configs["NB"], configs["SB"], configs["EB"], configs["WB"],
         site.control_type, site.stop_controlled, site.latitude, site.longitude, site.count_date,
         site.start_time, site.duration_hr, db.now_utc() if site.checked else None,
         json.dumps(site.notes)))


def qch_row(conn: sqlite3.Connection, sitenumber: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM speed_qch_site WHERE sitenumber = ?",
                        (sitenumber,)).fetchone()


def set_manual_site(conn: sqlite3.Connection, sitenumber: int, *, street_ns: str | None = None,
                    street_ew: str | None = None, location_name: str | None = None,
                    latitude: float | None = None, longitude: float | None = None) -> None:
    """SP-Q5: the user enters a site QC Hub does not have (drawer Setup). Four approaches are
    assumed; lane configs already edited for the sitenumber are kept."""
    row = qch_row(conn, sitenumber)
    if row is None:
        raise KeyError(sitenumber)
    clean = lambda v: (str(v).strip() or None) if v is not None else None
    conn.execute(
        "UPDATE speed_qch_site SET lookup = 'manual', street_ns = ?, street_ew = ?, "
        "location_name = ?, latitude = ?, longitude = ? WHERE sitenumber = ?",
        (clean(street_ns), clean(street_ew), clean(location_name), latitude, longitude, sitenumber))


def site_name(row: sqlite3.Row | None) -> str:
    """The QC Hub (or manually entered) name of a sitenumber: 'NS  -  EW', else the one name."""
    if row is None:
        return ""
    return qch_site.display_name(row["street_ns"], row["street_ew"], row["location_name"])


def lane_configs(conn: sqlite3.Connection, sitenumber: int) -> tuple[dict, dict]:
    """({direction: config in use}, {direction: config as QC Hub / the snapshot has it})."""
    row = qch_row(conn, sitenumber)
    base = {d: (row[f"{d.lower()}_lane_config"] if row else db.MANUAL_LANE_CONFIG) for d in DIRECTIONS}
    used = dict(base)
    for direction, config in conn.execute(
            "SELECT direction, lane_config FROM speed_lane_config WHERE sitenumber = ?", (sitenumber,)):
        used[direction] = config
    return used, base


def set_lane_config(conn: sqlite3.Connection, sitenumber: int, direction: str, config: str) -> None:
    """Store a drawer edit. An edit back to the snapshot's value removes the edit row."""
    _, base = lane_configs(conn, sitenumber)
    if config == base[direction]:
        conn.execute("DELETE FROM speed_lane_config WHERE sitenumber = ? AND direction = ?",
                     (sitenumber, direction))
    else:
        conn.execute(
            "INSERT INTO speed_lane_config (sitenumber, direction, lane_config, edited_utc) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (sitenumber, direction) DO UPDATE SET "
            "lane_config = excluded.lane_config, edited_utc = excluded.edited_utc",
            (sitenumber, direction, config, db.now_utc()))


def has_leg(config: str | None) -> bool:
    """An approach exists when its lane config has at least one lane."""
    return bool(config) and any(ch != "0" for ch in config)


# ------------------------------------------------------------------ time masks

def masked_hours(conn: sqlite3.Connection, sitenumber: int, cam: int) -> list[str]:
    """['YYYY-MM-DD HH', ...] sorted."""
    return [f"{d} {h:02d}" for d, h in conn.execute(
        "SELECT date, hour FROM speed_cam_mask WHERE sitenumber = ? AND cam = ? ORDER BY date, hour",
        (sitenumber, cam))]


def set_masked(conn: sqlite3.Connection, sitenumber: int, cam: int, keys: list[str], on: bool) -> None:
    """Mask or unmask date-hours ('YYYY-MM-DD HH'). Any change clears the cam's acknowledgement."""
    pairs = [(sitenumber, cam, k[:10], int(k[11:13])) for k in keys]
    if on:
        conn.executemany("INSERT OR IGNORE INTO speed_cam_mask (sitenumber, cam, date, hour) "
                         "VALUES (?, ?, ?, ?)", pairs)
    else:
        conn.executemany("DELETE FROM speed_cam_mask WHERE sitenumber = ? AND cam = ? AND date = ? "
                         "AND hour = ?", pairs)
    conn.execute("UPDATE speed_cam SET mask_ack_utc = NULL WHERE sitenumber = ? AND cam = ?",
                 (sitenumber, cam))


# ------------------------------------------------------------------ locations

def _hour_of(hhmmss: str | None) -> int:
    return int(hhmmss[:2]) if hhmmss else 0


def default_window(conn: sqlite3.Connection, sitenumber: int) -> dict:
    """SP-Q11: the count window from QC Hub when the site has one, else the file header."""
    q, s = qch_row(conn, sitenumber), sitenumber_row(conn, sitenumber)
    if q is not None and q["count_date"] and q["start_time"] and q["duration_hr"]:
        return {"start_date": q["count_date"], "start_time": q["start_time"],
                "duration_hr": q["duration_hr"], "source": db.WINDOW_QCH}
    return {"start_date": s["header_start_date"] if s else None,
            "start_time": s["header_start_time"] if s else None,
            "duration_hr": (s["header_duration_hr"] or None) if s else None,
            "source": db.WINDOW_HEADER}


def slice_window(window: dict, first: dict) -> dict:
    """The window of a location whose first slice has a date or an hour window (mockup newLoc)."""
    if not first.get("date") and first.get("from_hour") is None:
        return window
    start = first["from_hour"] if first.get("from_hour") is not None else _hour_of(window["start_time"])
    hours = window["duration_hr"] or 24
    end = first["to_hour"] if first.get("to_hour") is not None else min(
        24, start + (min(24, hours) if first.get("date") else hours))
    return {"start_date": first.get("date") or window["start_date"],
            "start_time": f"{int(start):02d}:00:00", "duration_hr": end - start,
            "source": db.WINDOW_SLICE}


def create_location(conn: sqlite3.Connection, slices: list[dict], *, nm_grouping_used: bool = False,
                    seed_from: int | None = None) -> int:
    """A report location: slices (sitenumber, optional date, optional from/to hour) and one
    decision row per direction.

    Fresh: a direction is in the report when the first sitenumber's lane config gives it a lane,
    with no posted speed, no source cam and the default mask. With `seed_from` (a split or an
    ungroup) the decisions, note, name and posted-speed links are copied from that location
    (SP-Q22); a copied source cam is kept only if its sitenumber is among the new slices.
    """
    if not slices:
        raise ValueError("a location needs at least one slice")
    first_site = slices[0]["sitenumber"]
    seed = load_location(conn, seed_from) if seed_from is not None else None
    base = ({"start_date": seed["window_start_date"], "start_time": seed["window_start_time"],
             "duration_hr": seed["window_duration_hr"], "source": seed["window_source"]}
            if seed else default_window(conn, first_site))
    window = slice_window(base, slices[0])
    now = db.now_utc()
    cur = conn.execute(
        "INSERT INTO speed_location (name_source, manual_name, note, nm_grouping_used, "
        "corners_linked, window_start_date, window_start_time, window_duration_hr, window_source, "
        "posted_same_ns, posted_same_ew, created_utc, updated_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (seed["name_source"] if seed else db.NAME_QCH, seed["manual_name"] if seed else None,
         seed["note"] if seed else "", int(nm_grouping_used),
         seed["corners_linked"] if seed else 1, window["start_date"], window["start_time"],
         window["duration_hr"], window["source"],
         seed["posted_same_ns"] if seed else 1, seed["posted_same_ew"] if seed else 1, now, now))
    location_id = cur.lastrowid
    for position, s in enumerate(slices, start=1):
        conn.execute(
            "INSERT INTO speed_slice (location_id, position, sitenumber, date, from_hour, to_hour) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (location_id, position, s["sitenumber"], s.get("date"), s.get("from_hour"), s.get("to_hour")))
    sites = {s["sitenumber"] for s in slices}
    used, _ = lane_configs(conn, first_site)
    for d in DIRECTIONS:
        if seed:
            row = seed["directions"][d]
            keep = row["source_sitenumber"] in sites
            values = (row["include_in_report"], row["posted_mph"],
                      row["source_sitenumber"] if keep else None, row["source_cam"] if keep else None,
                      row["adjust_by"], row["mask_low"], row["mask_high"])
        else:
            values = (int(has_leg(used[d])), None, None, None, 0, db.MASK_LOW_DEFAULT, None)
        conn.execute(
            "INSERT INTO speed_direction (location_id, direction, include_in_report, posted_mph, "
            "source_sitenumber, source_cam, adjust_by, mask_low, mask_high) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (location_id, d) + values)
    return location_id


def delete_location(conn: sqlite3.Connection, location_id: int) -> None:
    conn.execute("DELETE FROM speed_location WHERE location_id = ?", (location_id,))


def reset_location(conn: sqlite3.Connection, location_id: int) -> None:
    """SP-Q8: every decision back to where a fresh location starts (the slices stay)."""
    loc = load_location(conn, location_id)
    first = loc["slices"][0]
    window = slice_window(default_window(conn, first["sitenumber"]), first)
    conn.execute(
        "UPDATE speed_location SET name_source = ?, manual_name = NULL, note = '', "
        "corners_linked = 1, window_start_date = ?, window_start_time = ?, window_duration_hr = ?, "
        "window_source = ?, posted_same_ns = 1, posted_same_ew = 1, updated_utc = ? "
        "WHERE location_id = ?",
        (db.NAME_QCH, window["start_date"], window["start_time"], window["duration_hr"],
         window["source"], db.now_utc(), location_id))
    used, _ = lane_configs(conn, first["sitenumber"])
    for d in DIRECTIONS:
        conn.execute(
            "UPDATE speed_direction SET include_in_report = ?, posted_mph = NULL, "
            "source_sitenumber = NULL, source_cam = NULL, adjust_by = 0, mask_low = ?, "
            "mask_high = NULL WHERE location_id = ? AND direction = ?",
            (int(has_leg(used[d])), db.MASK_LOW_DEFAULT, location_id, d))


def touch_location(conn: sqlite3.Connection, location_id: int) -> None:
    conn.execute("UPDATE speed_location SET updated_utc = ? WHERE location_id = ?",
                 (db.now_utc(), location_id))


def location_ids(conn: sqlite3.Connection) -> list[int]:
    """Every location, in table order: by first sitenumber, then slice date, then slice hour."""
    return [r[0] for r in conn.execute(
        """SELECT l.location_id FROM speed_location l
           JOIN speed_slice s ON s.location_id = l.location_id AND s.position = 1
           ORDER BY s.sitenumber, COALESCE(s.date, ''), COALESCE(s.from_hour, -1), l.location_id""")]


def locations_on(conn: sqlite3.Connection, sitenumber: int) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT location_id FROM speed_slice WHERE sitenumber = ? ORDER BY location_id",
        (sitenumber,))]


def load_location(conn: sqlite3.Connection, location_id: int) -> dict:
    """The location row as a dict, with 'slices' (list of dicts) and 'directions' ({dir: dict})."""
    row = conn.execute("SELECT * FROM speed_location WHERE location_id = ?", (location_id,)).fetchone()
    if row is None:
        raise KeyError(location_id)
    out = dict(row)
    out["slices"] = [dict(r) for r in conn.execute(
        "SELECT sitenumber, date, from_hour, to_hour FROM speed_slice WHERE location_id = ? "
        "ORDER BY position", (location_id,))]
    out["directions"] = {r["direction"]: dict(r) for r in conn.execute(
        "SELECT * FROM speed_direction WHERE location_id = ?", (location_id,))}
    return out


# ------------------------------------------------------------------ delete / overwrite

def delete_sitenumber(conn: sqlite3.Connection, sitenumber: int) -> dict:
    """Remove a sitenumber and everything stored for it (SP-Q8).

    Events, cams, time masks, the QC Hub snapshot and lane config edits go with the row. Every
    location built only on this sitenumber is deleted. A grouped location that also holds other
    sitenumbers keeps those slices and has every decision reset (its source cams, masks and
    posted speeds were judged with this sitenumber's cams in view).

    Returns {'events', 'cams', 'locations_deleted': [...], 'locations_reset': [...]}.
    """
    events = conn.execute("SELECT COUNT(*) FROM speed_event WHERE sitenumber = ?",
                          (sitenumber,)).fetchone()[0]
    n_cams = conn.execute("SELECT COUNT(*) FROM speed_cam WHERE sitenumber = ?",
                          (sitenumber,)).fetchone()[0]
    deleted, reset = [], []
    for location_id in locations_on(conn, sitenumber):
        others = conn.execute("SELECT COUNT(*) FROM speed_slice WHERE location_id = ? AND "
                              "sitenumber <> ?", (location_id, sitenumber)).fetchone()[0]
        if others:
            reset.append(location_id)
        else:
            deleted.append(location_id)
    for location_id in deleted:
        delete_location(conn, location_id)
    # the row itself: cams, events, masks, snapshot, lane edits and remaining slices cascade
    conn.execute("DELETE FROM speed_event WHERE sitenumber = ?", (sitenumber,))
    conn.execute("DELETE FROM speed_sitenumber WHERE sitenumber = ?", (sitenumber,))
    for location_id in reset:
        reset_location(conn, location_id)
    return {"events": events, "cams": n_cams, "locations_deleted": deleted, "locations_reset": reset}
