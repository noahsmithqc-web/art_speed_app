"""Every change a user makes in the drawer or the table (plan section 7, SP-4).

One function per decision. Each validates, writes, and adds a paper-trail line (SP-Q15: every
PATCH, group / ungroup, split / unsplit is logged), inside the caller's transaction. A value
that can't be used raises EditError with a sentence for the user; nothing is written.

The wording of the paper trail follows the locked mockup's.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from art_speed import contract, loader, payload
from art_speed.storage import db, store

DIRECTIONS = contract.DIRECTIONS
AXES = contract.AXES
AXIS_OF = {d: axis for axis, pair in AXES.items() for d in pair}
MAX_POSTED, MAX_SPEED, MAX_ADJUST, MAX_WINDOW_HR = 100, 200, 30, 744


class EditError(ValueError):
    """The change can't be made. The message is written for the user."""


class NotFound(EditError):
    """The location, cam or sitenumber does not exist."""


# ------------------------------------------------------------------ helpers

def _location(conn: sqlite3.Connection, location_id: int) -> dict:
    try:
        return store.load_location(conn, location_id)
    except KeyError:
        raise NotFound(f"Location {location_id} does not exist.") from None


def _sites(loc: dict) -> list[int]:
    return list(dict.fromkeys(s["sitenumber"] for s in loc["slices"]))


def _direction(direction: str) -> str:
    if direction not in DIRECTIONS:
        raise EditError(f"{direction!r} is not a direction (NB, SB, EB, WB).")
    return direction


def _whole(value, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise EditError(f"{what} must be a whole number.")
    if not low <= value <= high:
        raise EditError(f"{what} must be between {low} and {high}.")
    return int(value)


def cam_ref(conn: sqlite3.Connection, loc: dict, cam) -> tuple[int, int]:
    """'17408305:3' (or just 3 when the location has one sitenumber) -> (sitenumber, cam)."""
    sites = _sites(loc)
    text = str(cam)
    m = re.fullmatch(r"(\d{8,9}):(\d+)", text)
    if m:
        site, number = int(m[1]), int(m[2])
    elif re.fullmatch(r"\d+", text) and len(sites) == 1:
        site, number = sites[0], int(text)
    else:
        raise EditError(f"{cam!r} is not a cam of this location (use sitenumber:cam).")
    if site not in sites or conn.execute("SELECT 1 FROM speed_cam WHERE sitenumber = ? AND cam = ?",
                                         (site, number)).fetchone() is None:
        raise NotFound(f"Cam {text} is not a cam of location {loc['location_id']}.")
    return site, number


def _cam_label(loc: dict, site: int, cam: int) -> str:
    return f"Cam {cam}" + (f" · {str(site)[6:]}" if len(_sites(loc)) > 1 else "")


def _blank(value) -> str:
    return "blank" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


# ------------------------------------------------------------------ directions

def set_include(conn, location_id: int, direction: str, on: bool) -> None:
    loc, d = _location(conn, location_id), _direction(direction)
    if not isinstance(on, bool):
        raise EditError("include_in_report must be true or false.")
    used, _ = store.lane_configs(conn, _sites(loc)[0])
    conn.execute("UPDATE speed_direction SET include_in_report = ? WHERE location_id = ? AND direction = ?",
                 (int(on), location_id, d))
    store.log(conn, f"{d} {'included in' if on else 'excluded from'} the report"
              + (" (overrides the QC Hub lane config)" if on != store.has_leg(used[d]) else ""),
              location_id=location_id)


def set_posted(conn, location_id: int, direction: str, value) -> None:
    """Posted speed. While the axis is "Same both ways" the value is written to both directions."""
    loc, d = _location(conn, location_id), _direction(direction)
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= MAX_POSTED:
            raise EditError(f"Posted speed must be a number above 0 and up to {MAX_POSTED} mph, or blank.")
    axis = AXIS_OF[d]
    if loc[f"posted_same_{axis.lower()}"]:
        a, b = AXES[axis]
        conn.execute("UPDATE speed_direction SET posted_mph = ? WHERE location_id = ? AND direction IN (?, ?)",
                     (value, location_id, a, b))
        store.log(conn, f"Posted speed {a} & {b} set to {_blank(value)}", location_id=location_id)
    else:
        conn.execute("UPDATE speed_direction SET posted_mph = ? WHERE location_id = ? AND direction = ?",
                     (value, location_id, d))
        store.log(conn, f"Posted speed {d} set to {_blank(value)}", location_id=location_id)


def set_posted_same(conn, location_id: int, axis: str, on: bool) -> None:
    """SP-Q6: the per-axis "Same both ways" tick. Ticking it writes one value to both directions
    (the first direction's, else the second's); unticking changes no value."""
    loc = _location(conn, location_id)
    if axis not in AXES or not isinstance(on, bool):
        raise EditError("posted_same takes NS or EW and true or false.")
    a, b = AXES[axis]
    conn.execute(f"UPDATE speed_location SET posted_same_{axis.lower()} = ? WHERE location_id = ?",
                 (int(on), location_id))
    if on:
        pa, pb = loc["directions"][a]["posted_mph"], loc["directions"][b]["posted_mph"]
        value = pa if pa is not None else pb
        conn.execute("UPDATE speed_direction SET posted_mph = ? WHERE location_id = ? AND direction IN (?, ?)",
                     (value, location_id, a, b))
    store.log(conn, f"{'N/S' if axis == 'NS' else 'E/W'} posted speed "
              + ("linked (same both ways)" if on else "unlinked (set each direction)"),
              location_id=location_id)


def set_source_cam(conn, location_id: int, direction: str, cam) -> None:
    """Pick the source cam of a direction (None clears it). A new source cam puts the speed mask
    back to the default - the mask was a judgement about the other cam's tail (v11)."""
    loc, d = _location(conn, location_id), _direction(direction)
    row = loc["directions"][d]
    had = row["source_cam"] is not None
    if cam is None:
        if not had:
            return
        conn.execute("UPDATE speed_direction SET source_sitenumber = NULL, source_cam = NULL "
                     "WHERE location_id = ? AND direction = ?", (location_id, d))
        store.log(conn, f"{d} source cam cleared", location_id=location_id)
        return
    site, number = cam_ref(conn, loc, cam)
    if (row["source_sitenumber"], row["source_cam"]) == (site, number):
        return
    suggested = payload._Location(loader.load(conn, location_id)).suggest(d)
    was_default = row["mask_low"] == db.MASK_LOW_DEFAULT and row["mask_high"] is None
    conn.execute("UPDATE speed_direction SET source_sitenumber = ?, source_cam = ?, mask_low = ?, "
                 "mask_high = NULL WHERE location_id = ? AND direction = ?",
                 (site, number, db.MASK_LOW_DEFAULT, location_id, d))
    label = _cam_label(loc, site, number).lower()
    note = ""
    if suggested:
        s_site, s_cam = suggested["cam"].split(":")
        note = (" (as suggested)" if (int(s_site), int(s_cam)) == (site, number)
                else f" (suggestion was {_cam_label(loc, int(s_site), int(s_cam)).lower()})")
    store.log(conn, f"{d} source cam set to {label}{note}"
              + (" · speed mask reset to default" if had and not was_default else ""),
              location_id=location_id)


def set_adjust(conn, location_id: int, direction: str, value) -> None:
    _location(conn, location_id)
    d, value = _direction(direction), _whole(value, "Adjust by", -MAX_ADJUST, MAX_ADJUST)
    conn.execute("UPDATE speed_direction SET adjust_by = ? WHERE location_id = ? AND direction = ?",
                 (value, location_id, d))
    store.log(conn, f"{d} adjust by set to {'+' if value >= 0 else ''}{value} mph", location_id=location_id)


def set_mask(conn, location_id: int, direction: str, low="keep", high="keep") -> None:
    """The two slider handles. `low` = 5 masks below 5 mph; None switches a handle off."""
    loc, d = _location(conn, location_id), _direction(direction)
    row = loc["directions"][d]
    new_low = row["mask_low"] if low == "keep" else (None if low is None else _whole(low, "Lower mask", 1, MAX_SPEED))
    new_high = row["mask_high"] if high == "keep" else (None if high is None else _whole(high, "Upper mask", 1, MAX_SPEED))
    if new_low is not None and new_high is not None and new_low >= new_high:
        raise EditError("The lower mask must be below the upper mask.")
    conn.execute("UPDATE speed_direction SET mask_low = ?, mask_high = ? WHERE location_id = ? AND direction = ?",
                 (new_low, new_high, location_id, d))
    if (new_low, new_high) == (db.MASK_LOW_DEFAULT, None) and (row["mask_low"], row["mask_high"]) != (new_low, new_high):
        store.log(conn, f"{d} mask reset to below 5 mph", location_id=location_id)
        return
    if new_low != row["mask_low"]:
        store.log(conn, f"{d} lower mask " + ("off" if new_low is None else f"set to below {new_low} mph"),
                  location_id=location_id)
    if new_high != row["mask_high"]:
        store.log(conn, f"{d} upper mask " + ("off" if new_high is None else f"set to above {new_high} mph"),
                  location_id=location_id)


def use_suggestions(conn, location_id: int) -> int:
    """Apply the suggested source cam to every included direction. Returns how many changed."""
    loc = _location(conn, location_id)
    working = payload._Location(loader.load(conn, location_id))
    changed = 0
    for d in working.included:
        suggested = working.suggest(d)
        if suggested and working.source[d] != suggested["cam"]:
            site, cam = suggested["cam"].split(":")
            conn.execute("UPDATE speed_direction SET source_sitenumber = ?, source_cam = ?, mask_low = ?, "
                         "mask_high = NULL WHERE location_id = ? AND direction = ?",
                         (int(site), int(cam), db.MASK_LOW_DEFAULT, loc["location_id"], d))
            changed += 1
    store.log(conn, f"Suggested source cams applied ({changed} change{'s' if changed > 1 else ''})"
              if changed else "Suggested source cams already in use", location_id=location_id)
    return changed


# ------------------------------------------------------------------ cams

def set_corner(conn, location_id: int, cam, corner) -> None:
    """A cam's corner (None clears it). With "corners linked", the same cam number on every
    sitenumber of the location gets the corner too."""
    loc = _location(conn, location_id)
    site, number = cam_ref(conn, loc, cam)
    if corner is not None and corner not in contract.CORNERS:
        raise EditError(f"{corner!r} is not a corner (NW, NE, SW, SE).")
    linked = bool(loc["corners_linked"]) and len(_sites(loc)) > 1
    targets = _sites(loc) if linked else [site]
    for n in targets:
        conn.execute("UPDATE speed_cam SET corner = ? WHERE sitenumber = ? AND cam = ?", (corner, n, number))
    store.log(conn, f"Cam {number} corner " + (f"set to {corner}" if corner else "cleared")
              + (" (all sitenumbers)" if linked else ""), sitenumber=site, cam=number)


def set_time_mask(conn, location_id: int, cam, hours: list, masked: bool) -> None:
    """7b: mask or unmask date-hours of a cam ('YYYY-MM-DD HH'). The mask belongs to the camera;
    any change clears its over-half acknowledgement."""
    loc = _location(conn, location_id)
    site, number = cam_ref(conn, loc, cam)
    if not isinstance(masked, bool) or not isinstance(hours, list) or not hours:
        raise EditError("A time mask needs a list of hours and masked true or false.")
    have = {k for bins in store.cam_bins(conn, site, number).values() for k in bins}
    for key in hours:
        if not isinstance(key, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} ([01]\d|2[0-3])", key):
            raise EditError(f"{key!r} is not an hour (YYYY-MM-DD HH).")
        if key not in have:
            raise EditError(f"Cam {number} has no data at {key}:00.")
    hours = sorted(set(hours))
    store.set_masked(conn, site, number, hours, masked)
    store.log(conn, f"Cam {number}: {'masked' if masked else 'unmasked'} {payload.describe_hours(hours)}",
              sitenumber=site, cam=number)


def acknowledge_mask(conn, location_id: int, cam) -> None:
    """SP-Q31: acknowledge "over half the hours masked". Refused when there is nothing to acknowledge."""
    loc = _location(conn, location_id)
    site, number = cam_ref(conn, loc, cam)
    drawer_cam = next(c for c in loader.drawer(conn, location_id)["cams"] if c["key"] == f"{site}:{number}")
    if not drawer_cam["over_half_masked"]:
        raise EditError(f"Cam {number} does not have over half its hours masked: nothing to acknowledge.")
    conn.execute("UPDATE speed_cam SET mask_ack_utc = ? WHERE sitenumber = ? AND cam = ?",
                 (db.now_utc(), site, number))
    store.log(conn, f"Cam {number}: over-half time mask acknowledged", sitenumber=site, cam=number)


# ------------------------------------------------------------------ the location

def set_note(conn, location_id: int, note) -> None:
    _location(conn, location_id)
    if not isinstance(note, str):
        raise EditError("The report note must be text.")
    conn.execute("UPDATE speed_location SET note = ? WHERE location_id = ?", (note.strip(), location_id))
    store.log(conn, f"Report note {'set' if note.strip() else 'cleared'}", location_id=location_id)


def set_name(conn, location_id: int, source=None, manual_name="keep") -> None:
    loc = _location(conn, location_id)
    source = source or loc["name_source"]
    if source not in db.NAME_SOURCES:
        raise EditError("The name source is qch or manual.")
    name = loc["manual_name"] if manual_name == "keep" else ((manual_name or "").strip() or None)
    if source == db.NAME_MANUAL and not name:
        raise EditError("Type the manual location name.")
    conn.execute("UPDATE speed_location SET name_source = ?, manual_name = ? WHERE location_id = ?",
                 (source, name, location_id))
    store.log(conn, f"Report name: manual ({name})" if source == db.NAME_MANUAL else "Report name: QC Hub",
              location_id=location_id)


def set_window(conn, location_id: int, window: dict) -> None:
    """SP-Q11: start date, start time and duration are editable; the source becomes 'edited'.
    Display and report metadata only - statistics always use the rows' own timestamps."""
    loc = _location(conn, location_id)
    if not isinstance(window, dict) or not set(window) <= {"start_date", "start_time", "duration_hours"}:
        raise EditError("The count window takes start_date, start_time and duration_hours.")
    if "start_date" in window:
        try:
            value = date.fromisoformat(str(window["start_date"])).isoformat()
        except ValueError:
            raise EditError("The start date must be YYYY-MM-DD.") from None
        conn.execute("UPDATE speed_location SET window_start_date = ? WHERE location_id = ?", (value, location_id))
        store.log(conn, f"Count window start date set to {value}", location_id=location_id)
    if "start_time" in window:
        value = payload.clock24(str(window["start_time"]))
        if value is None:
            raise EditError("The start time must be a time of day, e.g. 06:00 AM.")
        conn.execute("UPDATE speed_location SET window_start_time = ? WHERE location_id = ?", (value, location_id))
        store.log(conn, f"Count window start time set to {payload.clock12(value)}", location_id=location_id)
    if "duration_hours" in window:
        value = window["duration_hours"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= MAX_WINDOW_HR:
            raise EditError(f"The duration must be a number of hours above 0 and up to {MAX_WINDOW_HR}.")
        conn.execute("UPDATE speed_location SET window_duration_hr = ? WHERE location_id = ?", (value, location_id))
        store.log(conn, f"Count window duration set to {value:g} h", location_id=location_id)
    if window:
        conn.execute("UPDATE speed_location SET window_source = ? WHERE location_id = ?",
                     (db.WINDOW_EDITED, loc["location_id"]))


def set_lane_config(conn, location_id: int, direction: str, config) -> None:
    """SP-Q7: lane config edits go to the speed db only. A newly added approach is ticked "in
    report" on every location built on the sitenumber."""
    loc, d = _location(conn, location_id), _direction(direction)
    site = _sites(loc)[0]
    config = "" if config is None else str(config).strip()
    if not re.fullmatch(r"[0-7]{0,7}", config):
        raise EditError("A lane config is up to seven digits, each 0 to 7.")
    used, base = store.lane_configs(conn, site)
    was = used[d]
    if config == was:
        return
    store.set_lane_config(conn, site, d, config)
    extra = ""
    if not store.has_leg(was) and store.has_leg(config):
        conn.execute("UPDATE speed_direction SET include_in_report = 1 WHERE direction = ? AND location_id IN "
                     "(SELECT location_id FROM speed_slice WHERE sitenumber = ?)", (d, site))
        extra = f" · {d} now exists and is in the report"
    elif store.has_leg(was) and not store.has_leg(config):
        extra = f" · {d} has no lanes now (still in the report until you untick it)"
    store.log(conn, f"{d} lane config {was or 'none'} → {config or 'none'} (QC Hub: {base[d] or 'none'}){extra}",
              sitenumber=site)


def set_corners_linked(conn, location_id: int, on: bool) -> None:
    _location(conn, location_id)
    if not isinstance(on, bool):
        raise EditError("corners_linked must be true or false.")
    conn.execute("UPDATE speed_location SET corners_linked = ? WHERE location_id = ?", (int(on), location_id))
    store.log(conn, f"Corners {'linked' if on else 'unlinked'} across sitenumbers", location_id=location_id)


def set_manual_site(conn, location_id: int, site: dict) -> None:
    """SP-Q5: enter a site QC Hub does not have (or correct one it could not be checked against)."""
    loc = _location(conn, location_id)
    if not isinstance(site, dict):
        raise EditError("The site entry takes street_ns, street_ew, location_name, latitude, longitude.")
    number = int(site.get("sitenumber") or _sites(loc)[0])
    if number not in _sites(loc):
        raise NotFound(f"Sitenumber {number} is not part of location {location_id}.")
    for key in ("latitude", "longitude"):
        v = site.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            raise EditError(f"{key} must be a number.")
    if not any((site.get(k) or "").strip() for k in ("street_ns", "street_ew", "location_name")):
        raise EditError("Enter the street names or a location name.")
    store.set_manual_site(conn, number, street_ns=site.get("street_ns"), street_ew=site.get("street_ew"),
                          location_name=site.get("location_name"), latitude=site.get("latitude"),
                          longitude=site.get("longitude"))
    store.log(conn, f"Location entered by hand: {store.site_name(store.qch_row(conn, number))}", sitenumber=number)


# ------------------------------------------------------------------ group and split (SP-Q10, Q21, Q22)

def group(conn, location_ids: list) -> int:
    """Several one-sitenumber, unsplit locations -> one location. Decisions come from the first."""
    ids = [i for i in store.location_ids(conn) if i in set(location_ids)]
    if len(ids) < 2 or len(ids) != len(set(location_ids)):
        raise EditError("Select two or more locations to group.")
    locs = [_location(conn, i) for i in ids]
    for loc in locs:
        if len(loc["slices"]) != 1 or payload.is_sliced(loc["slices"]):
            raise EditError("Only unsplit, ungrouped locations can be grouped.")
    slices = [loc["slices"][0] for loc in locs]
    if len({s["sitenumber"] for s in slices}) != len(slices):
        raise EditError("A sitenumber can be in a group only once.")
    new = store.create_location(conn, slices, seed_from=ids[0])
    for i in ids:
        store.delete_location(conn, i)
    store.log(conn, "Grouped " + " + ".join(str(s["sitenumber"]) for s in slices) + " into one report location",
              location_id=new)
    return new


def ungroup(conn, location_id: int) -> list[int]:
    loc = _location(conn, location_id)
    if len(_sites(loc)) < 2:
        raise EditError("This location is not a group.")
    code = payload.location_code(loc["slices"])
    new = [store.create_location(conn, [s], seed_from=location_id) for s in loc["slices"]]
    store.delete_location(conn, location_id)
    for i in new:
        store.log(conn, f"Ungrouped from {code}", location_id=i)
    return new


def _single_site(loc: dict, what: str) -> int:
    sites = _sites(loc)
    if len(sites) != 1 or len(loc["slices"]) != 1:
        raise EditError(f"Only a location on one sitenumber can be {what}. Ungroup it first.")
    return sites[0]


def split_by_day(conn, location_id: int) -> list[int]:
    """One location per count day; each starts with the parent's decisions (SP-Q22)."""
    loc = _location(conn, location_id)
    site = _single_site(loc, "split by day")
    if loc["slices"][0]["date"]:
        raise EditError("This location is already one day.")
    days = loader.drawer(conn, location_id)["location"]["count_days"]
    if len(days) < 2:
        raise EditError("Only one count day: nothing to split.")
    new = []
    for day in days:
        i = store.create_location(conn, [{"sitenumber": site, "date": day}], seed_from=location_id)
        store.log(conn, f"Split {site} by day → {payload.day_label(day)}", location_id=i)
        new.append(i)
    store.delete_location(conn, location_id)
    return new


def split_by_time(conn, location_id: int, windows: list, keep_remainder: bool = True) -> list[int]:
    """One location per from-to window (whole hours, `to` exclusive). With keep_remainder the
    hours with data outside the windows become their own locations."""
    loc = _location(conn, location_id)
    site = _single_site(loc, "split by time")
    first = loc["slices"][0]
    if first["from_hour"] is not None:
        raise EditError("This location is already a time window.")
    clean = []
    for w in windows or []:
        if not isinstance(w, dict):
            raise EditError("Each window takes from and to.")
        a, b = _whole(w.get("from"), "A window's start hour", 0, 23), _whole(w.get("to"), "A window's end hour", 1, 24)
        if b <= a:
            raise EditError("A window's end hour must be after its start hour.")
        clean.append((a, b))
    clean.sort()
    if not clean:
        raise EditError("Add at least one window.")
    if any(clean[i][1] > clean[i + 1][0] for i in range(len(clean) - 1)):
        raise EditError("The windows overlap.")
    hours = sorted({int(k[11:13]) for k in payload._Location(loader.load(conn, location_id)).hour_keys()})
    new = []
    for a, b in clean:
        i = store.create_location(conn, [{"sitenumber": site, "date": first["date"], "from_hour": a, "to_hour": b}],
                                  seed_from=location_id)
        store.log(conn, f"Split {site} by time → {payload.hh(a)}{payload.DASH}{payload.hh(b)}", location_id=i)
        new.append(i)
    if keep_remainder and hours:
        cursor, end = hours[0], hours[-1] + 1
        gaps = []
        for a, b in clean:
            if a > cursor:
                gaps.append((cursor, a))
            cursor = max(cursor, b)
        if cursor < end:
            gaps.append((cursor, end))
        for a, b in gaps:
            i = store.create_location(conn, [{"sitenumber": site, "date": first["date"], "from_hour": a, "to_hour": b}],
                                      seed_from=location_id)
            store.log(conn, f"Remainder of time split: {payload.hh(a)}{payload.DASH}{payload.hh(b)}", location_id=i)
            new.append(i)
    store.delete_location(conn, location_id)
    return new


def unsplit(conn, location_id: int) -> int:
    """Put a sitenumber's split pieces back into one location, starting from the first piece's decisions."""
    loc = _location(conn, location_id)
    site = _single_site(loc, "unsplit")
    parts = [i for i in store.location_ids(conn)
             if (lambda p: len(p["slices"]) == 1 and p["slices"][0]["sitenumber"] == site)(store.load_location(conn, i))]
    if not any(payload.is_sliced(store.load_location(conn, i)["slices"]) for i in parts):
        raise EditError("This sitenumber is not split.")
    new = store.create_location(conn, [{"sitenumber": site}], seed_from=parts[0])
    for i in parts:
        store.delete_location(conn, i)
    store.log(conn, f"Unsplit {site} ({len(parts)} locations merged back)", location_id=new)
    return new
