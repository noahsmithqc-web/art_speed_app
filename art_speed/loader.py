"""From speed_######.db to the contract payloads (SP-3): the one place storage meets the engine.

    data = loader.load(conn, location_id)        # a payload.LocationData
    drawer = loader.drawer(conn, location_id)    # GET /speed/locations/{id}
    tab = loader.tab(conn)                       # GET /speed/locations
    everything = loader.fixtures(conn, project)  # the whole SP-0 file, for the CLI dump

The engine never sees the database: this module reads the rows (as hourly bins, see
store.cam_bins) and the decisions, and hands plain data to payload.build_drawer.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from art_speed import contract, payload
from art_speed.storage import store

DIRECTIONS = contract.DIRECTIONS


def local_minute(utc: str | None) -> str | None:
    """'2026-11-11T22:16:00Z' -> '2026-11-11 14:16' in the computer's time zone."""
    if not utc:
        return None
    moment = datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def _number(value):
    """25.0 -> 25, 12.5 -> 12.5, None -> None: whole values travel as whole numbers."""
    if value is None:
        return None
    return int(value) if float(value).is_integer() else float(value)


def _site(conn: sqlite3.Connection, sitenumber: int) -> payload.SiteData:
    q, s = store.qch_row(conn, sitenumber), store.sitenumber_row(conn, sitenumber)
    used, base = store.lane_configs(conn, sitenumber)
    cams = store.cams(conn, sitenumber)
    window = None
    if q is not None and q["count_date"] and q["start_time"] and q["duration_hr"]:
        window = {"start_date": q["count_date"], "start_time": q["start_time"],
                  "duration_hours": q["duration_hr"]}
    return payload.SiteData(
        sitenumber=str(sitenumber), name=store.site_name(q),
        street_ns=q["street_ns"] if q else None, street_ew=q["street_ew"] if q else None,
        qch_lookup=q["lookup"] if q else None,
        header={"sitecode": s["header_sitecode"], "start_date": s["header_start_date"],
                "start_time": payload.clock12(s["header_start_time"]),
                "duration_hours": _number(s["header_duration_hr"])},
        lane_config=used, lane_config_qch=base,
        latitude=q["latitude"] if q else None, longitude=q["longitude"] if q else None,
        qch_window=window,
        header_times={"start_date": s["header_start_date"], "start_time": s["header_start_time"],
                      "duration_hours": s["header_duration_hr"],
                      "form": cams[0]["csv_form"] if cams else "raw"})


def _trail(conn: sqlite3.Connection, location_id: int) -> list:
    out = []
    for r in store.trail(conn, location_id):
        entry = {"when": local_minute(r["logged_utc"]), "what": r["what"], "who": r["who"],
                 "scope": r["scope"]}
        if r["scope"] in ("site", "cam"):
            entry["sitenumber"] = str(r["sitenumber"])
        out.append(entry)
    return out


def load(conn: sqlite3.Connection, location_id: int) -> payload.LocationData:
    """Everything the engine needs about one location. Raises KeyError for an unknown id."""
    row = store.load_location(conn, location_id)
    numbers = list(dict.fromkeys(s["sitenumber"] for s in row["slices"]))
    cams = []
    for n in numbers:
        for c in store.cams(conn, n):
            cams.append(payload.CamData(
                sitenumber=str(n), cam=c["cam"], file=c["file_name"], csv_form=c["csv_form"],
                events=c["events"], corner=c["corner"],
                masked_hours=store.masked_hours(conn, n, c["cam"]),
                mask_acknowledged=c["mask_ack_utc"] is not None,
                bins=store.cam_bins(conn, n, c["cam"])))
    decisions = {}
    for d in DIRECTIONS:
        r = row["directions"][d]
        decisions[d] = {
            "include": bool(r["include_in_report"]), "posted": _number(r["posted_mph"]),
            "source_cam": (f"{r['source_sitenumber']}:{r['source_cam']}"
                           if r["source_cam"] is not None else None),
            "adjust_by": r["adjust_by"], "mask_low": r["mask_low"], "mask_high": r["mask_high"]}
    return payload.LocationData(
        id=location_id,
        slices=[{"sitenumber": str(s["sitenumber"]), "date": s["date"], "from_hour": s["from_hour"],
                 "to_hour": s["to_hour"]} for s in row["slices"]],
        sites=[_site(conn, n) for n in numbers], cams=cams, decisions=decisions,
        posted_same={"NS": bool(row["posted_same_ns"]), "EW": bool(row["posted_same_ew"])},
        window={"start_date": row["window_start_date"], "start_time": row["window_start_time"],
                "duration_hours": _number(row["window_duration_hr"]), "source": row["window_source"]},
        note=row["note"], name_source=row["name_source"], manual_name=row["manual_name"],
        nm_grouping_used=bool(row["nm_grouping_used"]), corners_linked=bool(row["corners_linked"]),
        trail=_trail(conn, location_id))


def drawer(conn: sqlite3.Connection, location_id: int) -> dict:
    return payload.build_drawer(load(conn, location_id))


def summary(conn: sqlite3.Connection) -> dict:
    one = lambda sql: conn.execute(sql).fetchone()[0]
    return {"sitenumbers": one("SELECT COUNT(*) FROM speed_sitenumber"),
            "locations": one("SELECT COUNT(*) FROM speed_location"),
            "cams": one("SELECT COUNT(*) FROM speed_cam"),
            "last_import": local_minute(one("SELECT MAX(imported_utc) FROM speed_import_log")),
            "qch_snapshot": local_minute(one("SELECT MAX(checked_utc) FROM speed_qch_site")),
            "last_report": local_minute(one("SELECT MAX(generated_utc) FROM speed_report_log"))}


def tab(conn: sqlite3.Connection, drawers: dict | None = None) -> dict:
    """The locations table. `drawers` ({id: drawer}) saves rebuilding ones the caller has."""
    drawers = drawers or {}
    rows = [payload.tab_row(drawers.get(i) or drawer(conn, i)) for i in store.location_ids(conn)]
    return {"summary": summary(conn), "generate": payload.generate_state(rows), "rows": rows}


def fixtures(conn: sqlite3.Connection, project) -> dict:
    """The whole contract file: project, tab and every drawer (the CLI dump; the mockup viewer
    reads it). `project` is a project.Project."""
    drawers = {i: drawer(conn, i) for i in store.location_ids(conn)}
    return {"contract_version": contract.CONTRACT_VERSION, "source": f"speed tool dump of {project.db_path.name}",
            "project": project.as_dict(), "tab": tab(conn, drawers),
            "locations": {str(i): d for i, d in drawers.items()},
            "viewer": {"fast_rows": {}, "open_location": next(iter(drawers), None)}}
