"""The speed router (plan section 7, SP-4).

    GET    /speed/project                               which project is open
    GET    /speed/locations                             the tab: summary, Generate state, one row per location
    GET    /speed/locations/{id}                        the drawer payload (the SP-0 shape)
    POST   /speed/import                                {folder, choices?, use_qch?, dry_run?} -> the import report
    DELETE /speed/sitenumbers/{sitenumber}              delete a sitenumber and its data
    POST   /speed/locations/group                       {ids: [...]}
    POST   /speed/locations/{id}/ungroup
    POST   /speed/locations/{id}/split-by-day
    POST   /speed/locations/{id}/split-by-time          {windows: [{from, to}], keep_remainder?}
    POST   /speed/locations/{id}/unsplit
    PATCH  /speed/locations/{id}/directions/{dir}       {posted?, include_in_report?, source_cam?, adjust_by?, mask?: {low?, high?}}
    PATCH  /speed/locations/{id}/cams/{cam}             {corner?, mask?: {hours: [...], masked}, acknowledge_mask?}
    PATCH  /speed/locations/{id}                        {name_source?, manual_name?, note?, window?, lane_config?,
                                                         posted_same?, corners_linked?, site?}
    POST   /speed/locations/{id}/use-suggestions
    GET    /speed/export/{sitenumber}/{cam}/{dir}       ?bin=50-59 | ?above=60 [&location=id] -> CSV download

What comes back:

* The two GETs return exactly the contract's `tab` and drawer shapes (art_speed/contract.py).
* Every PATCH and use-suggestions writes its paper-trail line(s) and returns the refreshed drawer
  payload, so the page never computes a statistic itself.
* Group / ungroup / split / unsplit / delete change which locations exist, so they return
  {"location_ids": [...], "tab": <the refreshed tab>}.
* A change that can't be made is HTTP 422 (404 for an unknown location, cam or sitenumber, 409
  when no project is open) with {"ok": false, "message": "<a sentence for the user>"}; nothing
  is written. (A.R.T.'s own routes answer 200 with ok: false; here a success is the bare contract
  payload, so the status code is what tells the two apart.)

The router is mounted the same way in the standalone shell and, later, in A.R.T. (SP-8): the
only thing it needs from its host is `get_project`, a function returning the open
project.Project or None.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Callable

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, Response

from art_speed import contract, edits, engine, loader, payload, project as project_mod
from art_speed.importer import run as import_run
from art_speed.log import get_logger
from art_speed.storage import db, store

log = get_logger(__name__)

DIRECTION_FIELDS = {"posted", "include_in_report", "source_cam", "adjust_by", "mask"}
CAM_FIELDS = {"corner", "mask", "acknowledge_mask"}
LOCATION_FIELDS = {"name_source", "manual_name", "note", "window", "lane_config", "posted_same",
                   "corners_linked", "site"}
EXPORT_COLUMNS = ("Date", "Time", "Direction", "Class", "Speed (In MPH)", "Track ID", "Sitenumber", "Cam")


class _Refused(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


def _refusal(status: int, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=status)


def _keys(body, allowed: set, what: str) -> dict:
    if not isinstance(body, dict):
        raise _Refused(422, f"{what} takes a JSON object.")
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise _Refused(422, f"{what} does not take {', '.join(unknown)}.")
    if not body:
        raise _Refused(422, f"{what} was sent with nothing to change.")
    return body


def empty_tab() -> dict:
    """The tab of a project with no speed data yet."""
    return {"summary": {"sitenumbers": 0, "locations": 0, "cams": 0, "last_import": None,
                        "qch_snapshot": None, "last_report": None},
            "generate": {"enabled": False, "green": False}, "rows": []}


def clock_12h(hhmmss: str) -> str:
    """'18:59:48' -> '06:59:48 PM', the form the Datalens export writes."""
    h, rest = int(hhmmss[:2]), hhmmss[2:]
    return f"{h % 12 or 12:02d}{rest} {'AM' if h < 12 else 'PM'}"


def create_router(get_project: Callable[[], project_mod.Project | None]) -> APIRouter:
    router = APIRouter(prefix="/speed")

    def project() -> project_mod.Project:
        proj = get_project()
        if proj is None:
            raise _Refused(409, "No project folder is open.")
        return proj

    def answer(work):
        """Run `work(project)`; turn every refusal into the {ok: false, message} answer."""
        try:
            return work(project())
        except _Refused as exc:
            return _refusal(exc.status, exc.message)
        except edits.NotFound as exc:
            return _refusal(404, str(exc))
        except edits.EditError as exc:
            return _refusal(422, str(exc))
        except db.NoSpeedData as exc:
            return _refusal(404, str(exc))
        except db.SpeedDbError as exc:
            return _refusal(422, str(exc))

    def with_db(proj, work, write: bool = False):
        conn = db.open_db(proj.folder, proj.number)
        try:
            if write:
                with db.transaction(conn):
                    return work(conn)
            return work(conn)
        finally:
            conn.close()

    def save(location_id: int, change):
        """A drawer save: the change and its paper trail in one transaction, then the drawer read again."""
        def work(proj):
            def inner(conn):
                change(conn)
                store.touch_location(conn, location_id)
                try:
                    return loader.drawer(conn, location_id)
                except KeyError:
                    raise edits.NotFound(f"Location {location_id} does not exist.") from None
            return with_db(proj, inner, write=True)
        return answer(work)

    def regroup(change):
        """A change to which locations exist: returns the new ids and the refreshed tab."""
        def work(proj):
            def inner(conn):
                ids = change(conn)
                return {"location_ids": ids if isinstance(ids, list) else [ids], "tab": loader.tab(conn)}
            return with_db(proj, inner, write=True)
        return answer(work)

    # ------------------------------------------------------------------ reads

    @router.get("/project")
    def get_project_info():
        def work(proj):
            return dict(proj.as_dict(), has_speed_data=db.exists(proj.folder, proj.number))
        return answer(work)

    @router.get("/locations")
    def get_locations():
        def work(proj):
            if not db.exists(proj.folder, proj.number):
                return empty_tab()
            return with_db(proj, loader.tab)
        return answer(work)

    @router.get("/locations/{location_id}")
    def get_location(location_id: int):
        def work(proj):
            def inner(conn):
                try:
                    return loader.drawer(conn, location_id)
                except KeyError:
                    raise edits.NotFound(f"Location {location_id} does not exist.") from None
            return with_db(proj, inner)
        return answer(work)

    # ------------------------------------------------------------------ import and delete

    @router.post("/import")
    def post_import(body: dict = Body(...)):
        def work(proj):
            data = _keys(body, {"folder", "choices", "use_qch", "dry_run"}, "The import")
            folder = data.get("folder")
            if not isinstance(folder, str) or not folder.strip():
                raise _Refused(422, "Pick the folder that holds the speed CSVs.")
            choices = data.get("choices") or {}
            if not isinstance(choices, dict) or any(v not in ("file name", "header") for v in choices.values()):
                raise _Refused(422, "choices maps a file name to 'file name' or 'header'.")
            report = import_run.import_folder(proj, folder, choices=choices,
                                              use_qch=bool(data.get("use_qch", True)),
                                              dry_run=bool(data.get("dry_run", False)))
            return report.as_dict()
        return answer(work)

    @router.delete("/sitenumbers/{sitenumber}")
    def delete_sitenumber(sitenumber: int):
        def work(proj):
            try:
                gone = import_run.delete_sitenumber(proj, sitenumber)
            except KeyError:
                raise edits.NotFound(f"Sitenumber {sitenumber} has no speed data.") from None
            return {"deleted": gone, "tab": with_db(proj, loader.tab)}
        return answer(work)

    # ------------------------------------------------------------------ group and split

    @router.post("/locations/group")
    def post_group(body: dict = Body(...)):
        ids = body.get("ids") if isinstance(body, dict) else None
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            return _refusal(422, "Grouping takes ids: a list of location ids.")
        return regroup(lambda conn: edits.group(conn, ids))

    @router.post("/locations/{location_id}/ungroup")
    def post_ungroup(location_id: int):
        return regroup(lambda conn: edits.ungroup(conn, location_id))

    @router.post("/locations/{location_id}/split-by-day")
    def post_split_by_day(location_id: int):
        return regroup(lambda conn: edits.split_by_day(conn, location_id))

    @router.post("/locations/{location_id}/split-by-time")
    def post_split_by_time(location_id: int, body: dict = Body(...)):
        windows = body.get("windows") if isinstance(body, dict) else None
        if not isinstance(windows, list):
            return _refusal(422, "Split by time takes windows: a list of {from, to}.")
        keep = body.get("keep_remainder", True)
        return regroup(lambda conn: edits.split_by_time(conn, location_id, windows, bool(keep)))

    @router.post("/locations/{location_id}/unsplit")
    def post_unsplit(location_id: int):
        return regroup(lambda conn: edits.unsplit(conn, location_id))

    # ------------------------------------------------------------------ decisions

    @router.patch("/locations/{location_id}/directions/{direction}")
    def patch_direction(location_id: int, direction: str, body: dict = Body(...)):
        def change(conn):
            data = _keys(body, DIRECTION_FIELDS, "A direction")
            if "include_in_report" in data:
                edits.set_include(conn, location_id, direction, data["include_in_report"])
            if "posted" in data:
                edits.set_posted(conn, location_id, direction, data["posted"])
            if "source_cam" in data:
                edits.set_source_cam(conn, location_id, direction, data["source_cam"])
            if "adjust_by" in data:
                edits.set_adjust(conn, location_id, direction, data["adjust_by"])
            if "mask" in data:
                mask = data["mask"]
                if not isinstance(mask, dict) or not set(mask) <= {"low", "high"} or not mask:
                    raise edits.EditError("mask takes low and / or high (a whole mph, or null for off).")
                edits.set_mask(conn, location_id, direction, mask.get("low", "keep") if "low" in mask else "keep",
                               mask.get("high", "keep") if "high" in mask else "keep")
        return save(location_id, change)

    @router.patch("/locations/{location_id}/cams/{cam}")
    def patch_cam(location_id: int, cam: str, body: dict = Body(...)):
        def change(conn):
            data = _keys(body, CAM_FIELDS, "A cam")
            if "corner" in data:
                edits.set_corner(conn, location_id, cam, data["corner"])
            if "mask" in data:
                mask = data["mask"]
                if not isinstance(mask, dict) or set(mask) != {"hours", "masked"}:
                    raise edits.EditError("mask takes hours (a list of 'YYYY-MM-DD HH') and masked (true or false).")
                edits.set_time_mask(conn, location_id, cam, mask["hours"], mask["masked"])
            if data.get("acknowledge_mask"):
                edits.acknowledge_mask(conn, location_id, cam)
        return save(location_id, change)

    @router.patch("/locations/{location_id}")
    def patch_location(location_id: int, body: dict = Body(...)):
        def change(conn):
            data = _keys(body, LOCATION_FIELDS, "A location")
            if "name_source" in data or "manual_name" in data:
                edits.set_name(conn, location_id, data.get("name_source"),
                               data["manual_name"] if "manual_name" in data else "keep")
            if "note" in data:
                edits.set_note(conn, location_id, data["note"])
            if "window" in data:
                edits.set_window(conn, location_id, data["window"])
            if "lane_config" in data:
                configs = data["lane_config"]
                if not isinstance(configs, dict) or not configs:
                    raise edits.EditError("lane_config maps a direction to its config, e.g. {\"EB\": \"2000000\"}.")
                for direction, config in configs.items():
                    edits.set_lane_config(conn, location_id, direction, config)
            if "posted_same" in data:
                same = data["posted_same"]
                if not isinstance(same, dict) or not same:
                    raise edits.EditError("posted_same maps NS or EW to true or false.")
                for axis, on in same.items():
                    edits.set_posted_same(conn, location_id, axis, on)
            if "corners_linked" in data:
                edits.set_corners_linked(conn, location_id, data["corners_linked"])
            if "site" in data:
                edits.set_manual_site(conn, location_id, data["site"])
        return save(location_id, change)

    @router.post("/locations/{location_id}/use-suggestions")
    def post_use_suggestions(location_id: int):
        return save(location_id, lambda conn: edits.use_suggestions(conn, location_id))

    # ------------------------------------------------------------------ the bin exports (v11 / v12)

    @router.get("/export/{sitenumber}/{cam}/{direction}")
    def get_export(sitenumber: int, cam: int, direction: str, bin: str | None = None,
                   above: int | None = None, location: int | None = None):
        def work(proj):
            if direction not in contract.DIRECTIONS:
                raise _Refused(422, f"{direction!r} is not a direction (NB, SB, EB, WB).")
            m = re.fullmatch(r"(\d{1,3})-(\d{1,3})", bin or "")
            if (bin is None) == (above is None) or (bin is not None and (not m or int(m[2]) < int(m[1]))):
                raise _Refused(422, "Give bin=50-59 or above=60, one of the two.")
            low, high = (int(m[1]), int(m[2])) if m else (above + 1, None)
            label = f"{low}-{high}mph" if m else f"above-{above}mph"

            def inner(conn):
                if conn.execute("SELECT 1 FROM speed_cam WHERE sitenumber = ? AND cam = ?",
                                (sitenumber, cam)).fetchone() is None:
                    raise edits.NotFound(f"Sitenumber {sitenumber} has no cam {cam}.")
                slices = [{}]
                if location is not None:
                    try:
                        loc = store.load_location(conn, location)
                    except KeyError:
                        raise edits.NotFound(f"Location {location} does not exist.") from None
                    slices = [s for s in loc["slices"] if s["sitenumber"] == sitenumber]
                masked = set(store.masked_hours(conn, sitenumber, cam))
                out = io.StringIO(newline="")
                writer = csv.writer(out, lineterminator="\r\n")
                writer.writerow(EXPORT_COLUMNS)
                for r in store.cam_rows(conn, sitenumber, cam, direction):
                    speed = r["speed_mph"]
                    if speed < low or (high is not None and speed > high) or speed == 0:
                        continue
                    key = engine.hour_key(r["date"], r["time"])
                    if key in masked or not engine.in_slices(slices, key):
                        continue
                    writer.writerow([r["date"], clock_12h(r["time"]), f"{r['direction']} {r['movement']}",
                                     r["class"] or "", speed, r["track_id"] or "", sitenumber, cam])
                return out.getvalue()

            text = with_db(proj, inner)
            name = f"{sitenumber}_cam-{cam}_{direction}_{label}.csv"
            return Response(text, media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})
        return answer(work)

    return router
