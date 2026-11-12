"""From a location's stored facts to the contract's drawer and tab payloads (SP-3).

    drawer = payload.build_drawer(location_data)      # the SP-0 shape, field for field
    row = payload.tab_row(drawer)

No database here: `LocationData` is plain data - the location's slices, its sitenumbers, its cams
with their hourly bins, and the decisions - and every number comes from engine.py. loader.py fills
a LocationData from speed_######.db; the tests also fill one straight from the fixture's inputs
and check that this module rebuilds the fixture's values (tests/test_engine_fixtures.py).

The rules that are not arithmetic live here, each in one place:

    cam group       in a grouped location (several sitenumbers) the same cam number on every
                    sitenumber is one camera: one choice, one corner, statistics over all of
                    its sitenumbers. Its key is its first member's (cam_groups)
    dead cam        every speed in the cam's file is zero (SP-Q9); a cam group is dead when
                    every member is
    corners ok      one cam: optional; several: every live cam has a corner
    suggestion      engine.suggest over the location's cams, only once corners are ok
    ready           corners ok, at least one direction in the report, and every included
                    direction has a source cam and a posted speed
    warnings        dead cam, off-leg events, hours outside the count window, over half the
                    hours masked (until acknowledged), header vs QC Hub, not in QC Hub; in a
                    grouped location also a cam that is not on every sitenumber and a member
                    with far fewer rows than the other cams of its sitenumber
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from art_speed import contract, engine

DIRECTIONS = contract.DIRECTIONS
DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DASH = "–"                       # en dash, as the mockup writes hour ranges


# ------------------------------------------------------------------ the input

@dataclass
class SiteData:
    sitenumber: str
    name: str = ""
    street_ns: str | None = None
    street_ew: str | None = None
    qch_lookup: str | None = None            # legacycounts | orderlocation | not_found | manual | None
    header: dict = field(default_factory=dict)       # sitecode, start_date, start_time (display), duration_hours
    lane_config: dict = field(default_factory=dict)        # {direction: config in use}
    lane_config_qch: dict = field(default_factory=dict)    # {direction: as QC Hub / the snapshot has it}
    latitude: float | None = None
    longitude: float | None = None
    qch_window: dict | None = None           # start_date, start_time ('HH:MM:SS'), duration_hours - when QC Hub gave one
    header_times: dict | None = None         # start_date, start_time ('HH:MM:SS'), duration_hours, form - from the files

    @property
    def sitecode(self) -> str:
        return self.sitenumber[6:]


@dataclass
class CamData:
    sitenumber: str
    cam: int
    file: str = ""
    csv_form: str = "raw"
    events: int = 0                          # the whole file
    corner: str | None = None
    masked_hours: list = field(default_factory=list)       # ['YYYY-MM-DD HH']
    mask_acknowledged: bool = False
    bins: dict = field(default_factory=dict)               # {direction: {'YYYY-MM-DD HH': {speed: rows}}}

    @property
    def key(self) -> str:
        return f"{self.sitenumber}:{self.cam}"


@dataclass
class LocationData:
    id: int
    slices: list                              # [{'sitenumber': str, 'date', 'from_hour', 'to_hour'}]
    sites: list                               # SiteData, in slice order, each once
    cams: list                                # CamData, by site then cam number
    decisions: dict                           # {direction: {'include', 'posted', 'source_cam', 'adjust_by', 'mask_low', 'mask_high'}}
    posted_same: dict = field(default_factory=lambda: {"NS": True, "EW": True})
    window: dict = field(default_factory=dict)             # start_date, start_time ('HH:MM:SS'), duration_hours, source
    note: str = ""
    name_source: str = "qch"
    manual_name: str | None = None
    nm_grouping_used: bool = False
    corners_linked: bool = True
    trail: list = field(default_factory=list)              # contract paper_trail entries, newest first


# ------------------------------------------------------------------ cam groups

def cam_groups(data: LocationData) -> dict:
    """{key: [CamData, ...]} - the cams of a location as the page counts them.

    One sitenumber: every cam on its own, under its own key. A grouped location: the same cam
    number on every sitenumber is one camera (a cam group), under the key of its first member
    in slice order, members in slice order, groups by cam number.
    """
    if len(data.sites) < 2:
        return {c.key: [c] for c in data.cams}
    order = {s.sitenumber: i for i, s in enumerate(data.sites)}
    by_number: dict = {}
    for c in sorted(data.cams, key=lambda c: order.get(c.sitenumber, len(order))):
        by_number.setdefault(c.cam, []).append(c)
    return {by_number[n][0].key: by_number[n] for n in sorted(by_number)}


def _all_zero(cam: CamData) -> bool:
    """SP-Q9: 100 % zero speeds over the whole file."""
    n = zero = 0
    for bins in cam.bins.values():
        for counts in bins.values():
            for speed, rows in counts.items():
                n += rows
                if speed == 0:
                    zero += rows
    return n > 0 and zero == n


def _pooled(members: list, site_slices: dict) -> CamData:
    """A cam group as one cam: every member's rows inside its own sitenumber's slices, added up
    hour by hour. The corner is the members' when they agree; the time mask is all of theirs."""
    bins: dict = {}
    for m in members:
        slices = site_slices[m.sitenumber]
        for direction, hours in m.bins.items():
            out = bins.setdefault(direction, {})
            for key, counts in hours.items():
                if not engine.in_slices(slices, key):
                    continue
                hour = out.setdefault(key, {})
                for speed, rows in counts.items():
                    hour[speed] = hour.get(speed, 0) + rows
    corners = {m.corner for m in members}
    first = members[0]
    return CamData(
        sitenumber=first.sitenumber, cam=first.cam, file=", ".join(m.file for m in members),
        csv_form="post-macro" if any(m.csv_form == "post-macro" for m in members) else "raw",
        events=sum(m.events for m in members), corner=first.corner if len(corners) == 1 else None,
        masked_hours=sorted({h for m in members for h in m.masked_hours}),
        mask_acknowledged=all(m.mask_acknowledged for m in members), bins=bins)


# ------------------------------------------------------------------ labels

def clock12(hhmmss: str | None) -> str | None:
    """'06:00:00' -> '06:00 AM' (the count window's start time as the page shows it)."""
    if not hhmmss:
        return None
    h, m = int(hhmmss[:2]), int(hhmmss[3:5])
    return f"{h % 12 or 12:02d}:{m:02d} {'AM' if h < 12 else 'PM'}"


def clock24(text: str | None) -> str | None:
    """'06:00 AM' / '6:00 pm' / '18:00' / '18:00:00' -> 'HH:MM:SS'; None when it is not a time."""
    if not text:
        return None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?\s*", text)
    if not m:
        return None
    h, mi, s = int(m[1]), int(m[2]), int(m[3] or 0)
    if m[4]:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if m[4].upper() == "PM" else 0)
    if h > 23 or mi > 59 or s > 59:
        return None
    return f"{h:02d}:{mi:02d}:{s:02d}"


def hh(hour: int) -> str:
    return f"{hour:02d}:00"


def day_label(iso: str) -> str:
    """'2026-10-06' -> 'Tue 10-06'."""
    return f"{DOW[date.fromisoformat(iso).weekday()]} {iso[5:]}"


def slice_label(s: dict) -> str:
    text = str(s["sitenumber"])
    if s.get("date"):
        text += " · " + day_label(s["date"])
    if s.get("from_hour") is not None:
        text += f" · {hh(s['from_hour'])}{DASH}{hh(s['to_hour'])}"
    return text


def is_sliced(slices: list) -> bool:
    return any(s.get("date") or s.get("from_hour") is not None for s in slices)


def location_code(slices: list) -> str:
    """'04'; grouped '01+02'; split '13 10-07' or '13 10-07 06–10'."""
    sites = list(dict.fromkeys(str(s["sitenumber"]) for s in slices))
    if len(sites) > 1:
        return "+".join(n[6:] for n in sites)
    s = slices[0]
    code = sites[0][6:]
    if s.get("date"):
        code += " " + s["date"][5:]
    if s.get("from_hour") is not None:
        code += f" {s['from_hour']:02d}{DASH}{s['to_hour']:02d}"
    return code


def describe_hours(keys: list) -> str:
    """Masked date-hours as the paper trail words them: '2026-10-07 10:00–12:00'; several days
    joined with '; ', several runs in a day with ', '. One hour alone: '2026-10-07 10:00'."""
    keys = sorted(keys)
    if len(keys) == 1:
        return f"{keys[0][:10]} {hh(int(keys[0][11:13]))}"
    by_day: dict = {}
    for k in keys:
        by_day.setdefault(k[:10], []).append(int(k[11:13]))
    parts = []
    for day, hours in by_day.items():
        runs, start, prev = [], hours[0], hours[0]
        for h in hours[1:]:
            if h == prev + 1:
                prev = h
                continue
            runs.append((start, prev))
            start = prev = h
        runs.append((start, prev))
        parts.append(day + " " + ", ".join(f"{hh(a)}{DASH}{hh(b + 1)}" for a, b in runs))
    return "; ".join(parts)


# ------------------------------------------------------------------ the drawer

class _Location:
    """One location's working set: the observed sets, cached, and the rules that read them."""

    def __init__(self, data: LocationData):
        self.d = data
        self.site_numbers = [s.sitenumber for s in data.sites]
        self.first = data.sites[0]
        self.site_slices = {n: [s for s in data.slices if str(s["sitenumber"]) == n]
                            for n in self.site_numbers}
        self.grouped = len(data.sites) > 1
        self.groups = cam_groups(data)                   # {key: member cams}
        self.key_of = {m.key: key for key, members in self.groups.items() for m in members}
        self.cams = ({key: _pooled(members, self.site_slices) for key, members in self.groups.items()}
                     if self.grouped else {c.key: c for c in data.cams})
        self.keys = list(self.cams)
        self._obs: dict = {}
        self.include = {d: bool(data.decisions[d]["include"]) for d in DIRECTIONS}
        self.included = [d for d in DIRECTIONS if self.include[d]]
        # a stored source cam of a grouped location may name any member: it means the group
        self.source = {d: self.key_of.get(data.decisions[d]["source_cam"]) for d in DIRECTIONS}

    # -- observed sets
    def slices_of(self, key: str) -> list:
        if self.grouped:                                 # a cam group's rows are already inside the slices
            return [{}]
        return self.site_slices[self.cams[key].sitenumber]

    def obs(self, key: str, direction: str) -> engine.Observed:
        hit = self._obs.get((key, direction))
        if hit is None:
            cam = self.cams[key]
            hit = engine.observe(cam.bins.get(direction, {}), self.slices_of(key), cam.masked_hours)
            self._obs[(key, direction)] = hit
        return hit

    def raw(self, key: str, direction: str) -> dict:
        return engine.raw_stats(self.obs(key, direction))

    def hour_keys_for(self, key: str) -> list:
        cam, slices = self.cams[key], self.slices_of(key)
        return sorted({k for bins in cam.bins.values() for k in bins if engine.in_slices(slices, k)})

    def hour_keys(self) -> list:
        return sorted({k for key in self.keys for k in self.hour_keys_for(key)})

    # -- cams
    def label(self, key: str) -> str:
        return f"Cam {self.cams[key].cam}"

    @staticmethod
    def member_label(cam: CamData) -> str:
        """One sitenumber's file of a cam group: 'Cam 3 · 30'."""
        return f"Cam {cam.cam} · {cam.sitenumber[6:]}"

    def dead(self, key: str) -> bool:
        """SP-Q9: 100 % zero speeds over the whole file - of every member, for a cam group."""
        return all(_all_zero(m) for m in self.groups[key])

    def member_rows(self, cam: CamData) -> int:
        """A member file's rows inside the location's slices, time masks not applied."""
        slices = self.site_slices[cam.sitenumber]
        return sum(rows for bins in cam.bins.values() for key, counts in bins.items()
                   if engine.in_slices(slices, key) for rows in counts.values())

    def group_warnings(self) -> list:
        """Grouped locations only: a cam that is not on every sitenumber, and a member file with
        under half the rows of the busiest cam of its sitenumber (the group's statistics then
        lean on the other sitenumbers)."""
        out = []
        for key, members in self.groups.items():
            have = {m.sitenumber for m in members}
            missing = [n for n in self.site_numbers if n not in have]
            if missing:
                out.append({"code": "partial_cam",
                            "text": f"{self.label(key)} is on {len(members)} of {len(self.site_numbers)} "
                                    f"sitenumbers (not on {', '.join(missing)})"})
        for n in self.site_numbers:
            rows = {m.key: (m, self.member_rows(m)) for members in self.groups.values()
                    for m in members if m.sitenumber == n}
            top = max((r for _, r in rows.values()), default=0)
            best = next((m for m, r in rows.values() if r == top), None)
            for m, r in rows.values():
                if top and r < top * contract.WEAK_MEMBER_BELOW and not _all_zero(m):
                    out.append({"code": "weak_member",
                                "text": f"{self.member_label(m)} has {r:,} event{'' if r == 1 else 's'} against {top:,} on "
                                        f"{self.member_label(best).lower()}"})
        return out

    def corners_ok(self) -> bool:
        return len(self.keys) < 2 or all(self.cams[k].corner for k in self.keys if not self.dead(k))

    def suggest(self, direction: str) -> dict | None:
        if not self.corners_ok():
            return None
        candidates = []
        for key in self.keys:
            st = self.raw(key, direction)
            candidates.append({"cam": key, "role": engine.role(self.cams[key].corner, direction),
                               "pct_zero": st["pct_zero"], "rows": st["n"],
                               "usable": not self.dead(key) and st["n_nonzero"] > 0})
        return engine.suggest(candidates)

    # -- masks
    def masked_total(self) -> dict:
        """Hours masked on the source cams, and the cams with over half their hours masked
        and not acknowledged."""
        out = {"n": 0, "over": []}
        seen = set()
        for d in self.included:
            key = self.source[d]
            if not key or key in seen:
                continue
            seen.add(key)
            cam, keys = self.cams[key], self.hour_keys_for(key)
            masked = sum(1 for k in keys if k in set(cam.masked_hours))
            out["n"] += masked
            if keys and masked > len(keys) / 2 and not cam.mask_acknowledged:
                out["over"].append({"cam": key, "label": self.label(key)})
        return out

    # -- status
    def ready(self) -> bool:
        return (self.corners_ok() and bool(self.included)
                and all(self.source[d] and self.d.decisions[d]["posted"] for d in self.included))

    def missing(self) -> list:
        out = []
        if not self.corners_ok():
            names = ", ".join(self.label(k) for k in self.keys if not self.cams[k].corner and not self.dead(k))
            out.append(f"Corners needed for every live cam ({names})")
        if not self.included:
            out.append("No approach included in the report")
        for d in self.included:
            if not self.source[d]:
                out.append(f"{d} has no source cam")
            if not self.d.decisions[d]["posted"]:
                out.append(f"{d} has no posted speed")
        return out

    def window_keys(self) -> set | None:
        w = self.d.window
        if not (w.get("start_date") and w.get("duration_hours")):
            return None
        start = datetime.fromisoformat(f"{w['start_date']}T{(w.get('start_time') or '00:00:00')[:2]}:00:00")
        return {(start + timedelta(hours=i)).strftime("%Y-%m-%d %H")
                for i in range(engine.js_round(w["duration_hours"]))}

    def warnings(self) -> list:
        out = []
        dead = [self.label(k) for k in self.keys if self.dead(k)]
        if self.grouped:                                 # one sitenumber's file of a live cam group
            dead += [self.member_label(m) for k in self.keys if not self.dead(k)
                     for m in self.groups[k] if _all_zero(m)]
        if dead:
            out.append({"code": "dead_cam", "text": ", ".join(dead) + " dead (100 % zero speeds)"})
        if self.grouped:
            out += self.group_warnings()
        off = {}
        for d in DIRECTIONS:
            if not self.include[d]:
                n = sum(self.obs(k, d).n for k in self.keys)
                if n:
                    off[d] = n
        if off:
            out.append({"code": "off_leg", "text": ", ".join(f"{n} {d} events" for d, n in off.items())
                        + " in approaches not included in the report (ignored)"})
        inside = self.window_keys()
        if inside is not None:
            outside = sum(1 for k in self.hour_keys() if k not in inside)
            if outside:
                out.append({"code": "outside_window", "text": f"{outside} hour{'s' if outside > 1 else ''} "
                                                              "of data outside the count window"})
        for item in self.masked_total()["over"]:
            out.append({"code": "over_half_masked", "text": f"{item['label']}: over half the hours masked",
                        "cam": item["cam"], "can_acknowledge": True})
        for site in self.d.sites:
            out += _site_warnings(site, len(self.d.sites) > 1)
        return out


def _site_warnings(site: SiteData, name_it: bool) -> list:
    """Lasting warnings from the import: the file header against QC Hub, and a site QC Hub lacks."""
    out, who = [], (f"{site.sitenumber}: " if name_it else "")
    q, h = site.qch_window, site.header_times
    if q and h:
        diffs = []
        if h.get("start_date") and q.get("start_date") and h["start_date"] != q["start_date"]:
            diffs.append(f"start date {h['start_date']} vs {q['start_date']}")
        if h.get("start_time") and q.get("start_time") and h["start_time"] != q["start_time"]:
            diffs.append(f"start time {h['start_time'][:5]} vs {q['start_time'][:5]}")
        if (h.get("form") != "post-macro" and h.get("duration_hours") and q.get("duration_hours")
                and abs(h["duration_hours"] - q["duration_hours"]) > 0.01):
            diffs.append(f"duration {h['duration_hours']:g} h vs {q['duration_hours']:g} h")
        if diffs:
            out.append({"code": "header_vs_qch",
                        "text": f"{who}File header disagrees with QC Hub ({'; '.join(diffs)})"})
    if site.qch_lookup == "not_found":
        out.append({"code": "not_in_qch", "text": f"{who}Not in QC Hub: four approaches assumed; "
                                                  "enter the location in Setup"})
    elif site.qch_lookup is None:
        out.append({"code": "not_in_qch", "text": f"{who}Not checked against QC Hub: four approaches assumed"})
    return out


def _slices_out(slices: list) -> list:
    return [{"sitenumber": str(s["sitenumber"]), "date": s.get("date"), "from_hour": s.get("from_hour"),
             "to_hour": s.get("to_hour"), "label": slice_label(s)} for s in slices]


def _window_out(w: dict) -> dict:
    return {"start_date": w.get("start_date"), "start_time": clock12(w.get("start_time")),
            "duration_hours": w.get("duration_hours"), "source": w.get("source") or "file header"}


def _report(loc: _Location, direction: str) -> dict | None:
    key = loc.source[direction]
    if not key:
        return None
    dec = loc.d.decisions[direction]
    f = engine.final_stats(loc.obs(key, direction), low=dec["mask_low"], high=dec["mask_high"],
                           adjust_by=dec["adjust_by"] or 0, posted=dec["posted"])
    return {"source_cam": key, "adjust_by": dec["adjust_by"] or 0, "events": f["events"], "zero": f["zero"],
            "pct_zero": f["pct_zero"], "masked": f["masked"], "pct_masked": f["pct_masked"], "n": f["n"],
            "avg": f["avg"], "p85": f["p85"], "p95": f["p95"], "over_posted": f["over_posted"],
            "pct_over_posted": f["pct_over_posted"], "masked_hours": f["masked_hours"]}


def _direction(loc: _Location, direction: str) -> dict:
    dec, site = loc.d.decisions[direction], loc.first
    config = site.lane_config.get(direction) or ""
    return {"exists": any(ch != "0" for ch in config), "include_in_report": loc.include[direction],
            "posted": dec["posted"], "lane_config": config,
            "lane_config_qch": site.lane_config_qch.get(direction) or "",
            "source_cam": loc.source[direction], "suggested": loc.suggest(direction),
            "adjust_by": dec["adjust_by"] or 0,
            "mask": {"low": dec["mask_low"], "high": dec["mask_high"],
                     "is_default": dec["mask_low"] == contract.MASK_LOW_DEFAULT and dec["mask_high"] is None},
            "report": _report(loc, direction) if loc.include[direction] else None,
            "off_leg_events": 0 if loc.include[direction] else sum(loc.obs(k, direction).n for k in loc.keys)}


def _cam(loc: _Location, key: str) -> dict:
    cam, keys = loc.cams[key], loc.hour_keys_for(key)
    masked = sum(1 for k in keys if k in set(cam.masked_hours))
    directions = {}
    for d in DIRECTIONS:
        st = loc.raw(key, d)
        directions[d] = {"n": st["n"], "zero": st["zero"], "n_nonzero": st["n_nonzero"],
                         "pct_zero": st["pct_zero"], "avg": st["avg"], "p85": st["p85"],
                         "role": engine.role(cam.corner, d)}
    out = {"key": key, "sitenumber": cam.sitenumber, "cam": cam.cam, "label": loc.label(key),
           "corner": cam.corner, "file": cam.file, "csv_form": cam.csv_form, "events": cam.events,
           "events_in_location": sum(loc.obs(key, d).n for d in DIRECTIONS), "dead": loc.dead(key),
           "masked_hours": sorted(cam.masked_hours), "mask_acknowledged": bool(cam.mask_acknowledged),
           "hours_with_data": len(keys), "over_half_masked": bool(keys) and masked > len(keys) / 2,
           "directions": directions}
    if loc.grouped:                                      # a cam group: its files, one per sitenumber
        members = loc.groups[key]
        out["on_sitenumbers"] = {"set": len(members), "of": len(loc.site_numbers)}
        out["members"] = [{
            "key": m.key, "sitenumber": m.sitenumber, "cam": m.cam, "label": loc.member_label(m),
            "corner": m.corner, "file": m.file, "csv_form": m.csv_form, "events": m.events,
            "events_in_location": sum(
                engine.observe(m.bins.get(d, {}), loc.site_slices[m.sitenumber], m.masked_hours).n
                for d in DIRECTIONS),
            "dead": _all_zero(m), "masked_hours": sorted(m.masked_hours),
            "mask_acknowledged": bool(m.mask_acknowledged)} for m in members]
    return out


def _histogram(loc: _Location, key: str, direction: str) -> dict | None:
    obs = loc.obs(key, direction)
    if not obs.n:
        return None
    dec = loc.d.decisions[direction]
    upper = dec["mask_high"] if loc.source[direction] == key else None
    speeds = sorted(obs.hist)
    return {"events": obs.n, "zero": obs.zero, "masked_hours": obs.masked_hours,
            "bins": {str(s): obs.hist[s] for s in speeds}, "occupied": speeds,
            "min_speed": speeds[0] if speeds else None, "max_speed": speeds[-1] if speeds else None,
            "bins10": engine.bins10(obs.hist, upper)}


def _hourly(loc: _Location) -> dict:
    keys = loc.hour_keys()
    dates = sorted({k[:10] for k in keys})
    top = None
    directions: dict = {}
    for d in loc.included:
        key = loc.source[d]
        if not key:
            directions[d] = None
            continue
        cam, dec = loc.cams[key], loc.d.decisions[d]
        bins, masked, slices = cam.bins.get(d, {}), set(cam.masked_hours), loc.slices_of(key)
        days = []
        for day in dates:
            cells, day_kept = [], {}
            for hour in range(24):
                k = f"{day} {hour:02d}"
                counts = bins.get(k)
                if not counts or not engine.in_slices(slices, k):
                    cells.append(None)
                    continue
                st = engine.hour_stats(counts, low=dec["mask_low"], high=dec["mask_high"],
                                       adjust_by=dec["adjust_by"] or 0)
                is_masked = k in masked
                if not is_masked:
                    for speed, rows in st["kept"].items():
                        day_kept[speed] = day_kept.get(speed, 0) + rows
                    if st["n"] >= contract.THIN_BELOW and (top is None or st["p85"] > top):
                        top = st["p85"]
                cells.append({"hour": hour, "rows": st["rows"], "zero": st["zero"], "n": st["n"],
                              "p85": st["p85"], "p95": st["p95"], "masked": is_masked, "peak": False})
            live = [engine.js_round(c["p85"]) for c in cells
                    if c and c["n"] >= contract.BLANK_BELOW and not c["masked"]]
            if live:
                best = max(live)
                for c in cells:
                    if c and c["n"] >= contract.BLANK_BELOW and not c["masked"] and engine.js_round(c["p85"]) == best:
                        c["peak"] = True
            days.append({"date": day, "n": sum(day_kept.values()), "p85": engine.percentile_inc(day_kept, 0.85),
                         "p95": engine.percentile_inc(day_kept, 0.95), "cells": cells})
        directions[d] = {"source_cam": key,
                         "masked_hours": sum(1 for k in keys if k in masked and bins.get(k)),
                         "shared_with": [x for x in loc.included if x != d and loc.source[x] == key],
                         "days": days}
    return {"dates": dates, "hours_with_data": len(keys), "peak_hours": list(contract.PEAK_HOURS),
            "scale_max": top if top is not None else 1, "thin_below": contract.THIN_BELOW,
            "blank_below": contract.BLANK_BELOW, "directions": directions}


def location_name(data: LocationData) -> str:
    if data.name_source == "manual" and data.manual_name:
        return data.manual_name
    return data.sites[0].name


def build_drawer(data: LocationData) -> dict:
    """The drawer payload of one location, in the contract's shape."""
    loc = _Location(data)
    first = loc.first
    status_ready = loc.ready()
    histograms = {}
    for key in loc.keys:
        histograms[key] = {}
        for d in DIRECTIONS:
            h = _histogram(loc, key, d)
            if h:
                histograms[key][d] = h
    return {
        "location": {
            "id": data.id, "code": location_code(data.slices), "name": location_name(data),
            "name_source": data.name_source, "manual_name": data.manual_name, "qch_name": first.name or None,
            "sitenumbers": [{"sitenumber": s.sitenumber, "sitecode": s.sitecode, "name": s.name,
                             "street_ns": s.street_ns, "street_ew": s.street_ew, "qch_lookup": s.qch_lookup,
                             "header": {"sitecode": s.header.get("sitecode"), "start_date": s.header.get("start_date"),
                                        "start_time": s.header.get("start_time"),
                                        "duration_hours": s.header.get("duration_hours")}}
                            for s in data.sites],
            "slices": _slices_out(data.slices), "is_sliced": is_sliced(data.slices),
            "is_grouped": len(data.sites) > 1, "latitude": first.latitude, "longitude": first.longitude,
            "geometry_source": "qch" if first.qch_lookup == "legacycounts" else "manual",
            "nm_grouping_used": bool(data.nm_grouping_used), "corners_linked": bool(data.corners_linked),
            "window": _window_out(data.window), "count_days": sorted({k[:10] for k in loc.hour_keys()}),
            "cams": len(loc.keys), "events": sum(loc.obs(k, d).n for k in loc.keys for d in DIRECTIONS),
            "masked_hours": loc.masked_total()["n"]},
        "posted_same": {"NS": bool(data.posted_same["NS"]), "EW": bool(data.posted_same["EW"])},
        "directions": {d: _direction(loc, d) for d in DIRECTIONS},
        "cams": [_cam(loc, k) for k in loc.keys],
        "histograms": histograms,
        "hourly": _hourly(loc),
        # every file's own bins: in a grouped location under the member keys, not the cam group's
        "bins": {c.key: {d: {hk: {str(s): n for s, n in sorted(counts.items())} for hk, counts in bins.items()}
                         for d, bins in c.bins.items()} for c in data.cams},
        "note": data.note or "",
        "status": {"state": contract.READY if status_ready else contract.NEEDS_INPUT,
                   "label": contract.STATUS_LABELS[contract.READY if status_ready else contract.NEEDS_INPUT],
                   "missing": loc.missing()},
        "warnings": loc.warnings(),
        "paper_trail": list(data.trail),
    }


def tab_row(drawer: dict) -> dict:
    """The locations-table row of a location, from its drawer payload."""
    loc, dirs = drawer["location"], drawer["directions"]
    included = [d for d in DIRECTIONS if dirs[d]["include_in_report"]]
    return {"id": loc["id"], "code": loc["code"], "name": loc["name"],
            "sitenumbers": [s["sitenumber"] for s in loc["sitenumbers"]], "slices": loc["slices"],
            "is_sliced": loc["is_sliced"], "is_grouped": loc["is_grouped"], "window": loc["window"],
            "cams": loc["cams"], "in_report": included, "events": loc["events"],
            "corners": {"set": sum(1 for c in drawer["cams"] if c["corner"]), "of": len(drawer["cams"])},
            "posted": {"set": sum(1 for d in included if dirs[d]["posted"]), "of": len(included)},
            "source_cams": {"set": sum(1 for d in included if dirs[d]["source_cam"]), "of": len(included)},
            "status": drawer["status"], "warnings": [w["text"] for w in drawer["warnings"]]}


def generate_state(rows: list) -> dict:
    """SP-Q16/Q26, the near miss green rule: enabled with at least one Ready location, green
    when every location is Ready."""
    states = [r["status"]["state"] for r in rows]
    any_ready = contract.READY in states
    return {"enabled": any_ready, "green": any_ready and all(s == contract.READY for s in states)}


# ------------------------------------------------------------------ fixture -> LocationData

def from_drawer(drawer: dict) -> LocationData:
    """The inputs of a drawer payload as a LocationData (the fixture's, or any dump's).

    Only what a user or an import supplies is read - slices, sites, cams with their bins,
    corners, masks, decisions, window, note, trail - never a computed value. Rebuilding the
    drawer from the result must give the same payload: that is the engine's fixture test.
    """
    loc, dirs = drawer["location"], drawer["directions"]
    sites = []
    for i, s in enumerate(loc["sitenumbers"]):
        sites.append(SiteData(
            sitenumber=s["sitenumber"], name=s["name"], street_ns=s["street_ns"], street_ew=s["street_ew"],
            qch_lookup=s["qch_lookup"], header=dict(s["header"]),
            lane_config={d: dirs[d]["lane_config"] for d in DIRECTIONS} if i == 0 else {},
            lane_config_qch={d: dirs[d]["lane_config_qch"] for d in DIRECTIONS} if i == 0 else {},
            latitude=loc["latitude"] if i == 0 else None, longitude=loc["longitude"] if i == 0 else None))
    files = [m for c in drawer["cams"] for m in c.get("members", [c])]      # a cam group's members
    order = {s.sitenumber: i for i, s in enumerate(sites)}
    files.sort(key=lambda c: (order.get(c["sitenumber"], len(order)), c["cam"]))
    cams = [CamData(sitenumber=c["sitenumber"], cam=c["cam"], file=c["file"], csv_form=c["csv_form"],
                    events=c["events"], corner=c["corner"], masked_hours=list(c["masked_hours"]),
                    mask_acknowledged=c["mask_acknowledged"],
                    bins={d: {hk: {int(s): n for s, n in counts.items()} for hk, counts in bins.items()}
                          for d, bins in drawer["bins"].get(c["key"], {}).items()})
            for c in files]
    w = loc["window"]
    return LocationData(
        id=loc["id"],
        slices=[{"sitenumber": s["sitenumber"], "date": s["date"], "from_hour": s["from_hour"],
                 "to_hour": s["to_hour"]} for s in loc["slices"]],
        sites=sites, cams=cams,
        decisions={d: {"include": dirs[d]["include_in_report"], "posted": dirs[d]["posted"],
                       "source_cam": dirs[d]["source_cam"], "adjust_by": dirs[d]["adjust_by"],
                       "mask_low": dirs[d]["mask"]["low"], "mask_high": dirs[d]["mask"]["high"]}
                   for d in DIRECTIONS},
        posted_same=dict(drawer["posted_same"]),
        window={"start_date": w["start_date"], "start_time": clock24(w["start_time"]),
                "duration_hours": w["duration_hours"], "source": w["source"]},
        note=drawer["note"], name_source=loc["name_source"], manual_name=loc["manual_name"],
        nm_grouping_used=loc["nm_grouping_used"], corners_linked=loc["corners_linked"],
        trail=list(drawer["paper_trail"]))
