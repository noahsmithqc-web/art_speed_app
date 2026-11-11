"""What QC Hub knows about one speed sitenumber (plan SP-1, SP-Q5).

    found = site.lookup_sites([17408304, 17408305])     # {sitenumber: Site}; never raises

Adapted from A.R.T.'s turn_movement/lookup.py and model.py (the manual turn movement import's
lookup): the same three queries, the same two steps, the same value shaping - returned as the
few fields the speed tool keeps instead of a turn movement roster row.

  1. legacycounts by SiteNumber: QC Hub has the site as a turn movement count - street names,
     city, state, lane configs, control, the scheduled count date and times, plus lat/long from
     orderlocation. Outcome 'legacycounts'.
  2. Otherwise orderlocationtime -> orderlocation -> orderlocationstreet: QC Hub has the site as
     a tube or survey location - one name, city, state, lat/long; no lane configs or times.
     Outcome 'orderlocation'.
  3. Neither: 'not_found' - the user enters the site in the drawer's Setup section.

QC Hub not reachable, no login, or a query that runs long: outcome None = not checked. The import
never stalls on QC Hub (SP-Q5).

The count window (SP-Q11) comes from the legacycounts rows: the earliest scheduled date, its
start time, and a duration of (EndTime - StartTime) for one scheduled date; when the sitenumber
has several scheduled dates the window runs from the first day's start to the last day's end.
An EndTime at or before the StartTime means the count runs to that time the next day (24 h when
they are equal).
"""

from __future__ import annotations

import re
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from art_speed import config
from art_speed.log import get_logger
from art_speed.qch import connection

log = get_logger(__name__)

LEGACYCOUNTS = "legacycounts"
ORDERLOCATION = "orderlocation"
NOT_FOUND = "not_found"
MANUAL = "manual"                    # entered by the user in Setup; never returned from here
LOOKUPS = (LEGACYCOUNTS, ORDERLOCATION, NOT_FOUND, MANUAL)
DIRECTIONS = ("NB", "SB", "EB", "WB")
STREET_JOINER = " / "                # several orderlocationstreet rows -> one name
NAME_SEPARATOR = "  -  "             # 'NS  -  EW', as A.R.T. prints a QC Hub name

# ------------------------------------------------------------------ SQL (as in A.R.T.)

LEGACYCOUNTS_COLUMNS = (
    "QCOrderNo", "SiteNumber", "StreetNameNorthSouth", "StreetNameEastWest", "City", "State",
    "DateScheduledForCount", "StartTime", "EndTime",
    "NBLaneConfig", "SBLaneConfig", "EBLaneConfig", "WBLaneConfig",
    "ControlType", "StopControlled",
)

# One row per scheduled count date for the SiteNumber (the table's grain); the latest first.
SITE_LEGACYCOUNTS_SQL = f"""
    SELECT {", ".join(f"lc.{c}" for c in LEGACYCOUNTS_COLUMNS)}
    FROM legacycounts lc
    WHERE lc.SiteNumber = %s
    ORDER BY lc.DateScheduledForCount DESC
"""

# Every location the sitenumber points at (one sitenumber can point at several).
SITE_LOCATION_SQL = """
    SELECT o.OrderLocationID, o.City, o.StateCode, o.Latitude, o.Longitude
    FROM orderlocationtime t
    JOIN orderlocation o ON o.OrderLocationID = t.OrderLocationID
    WHERE t.SiteNumber = %s
    ORDER BY o.OrderLocationID
"""

# The MAIN row first; a tube/survey site has just the one row.
SITE_STREET_SQL = """
    SELECT s.StreetName, s.StreetDirectionCode
    FROM orderlocationstreet s
    WHERE s.OrderLocationID = %s
    ORDER BY CASE WHEN s.StreetDirectionCode = 'MAIN' THEN 0 ELSE 1 END, s.ClockwiseStreetOrder
"""


# ------------------------------------------------------------------ turning QC Hub values into ours

class BadValue(ValueError):
    """A QC Hub value that can't be turned into the shape the speed db stores."""


_DATE_TEXT = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[ T].*)?$")
_TIME_TEXT = re.compile(r"^\s*(?:\d{4}-\d{2}-\d{2}[ T])?(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*$")


def as_date(value) -> str | None:
    """A DATETIME / date / 'YYYY-MM-DD[ hh:mm:ss]' -> 'YYYY-MM-DD'. The time part is dropped."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    m = _DATE_TEXT.match(str(value))
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            pass
    raise BadValue(f"{value!r} is not a date")


def as_time(value) -> str | None:
    """A DATETIME ('1900-01-01 07:00:00') / TIME (timedelta) / time / text -> 'HH:MM:SS'."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, timedelta):           # mysql-connector's type for a TIME column
        seconds = int(value.total_seconds())
        if 0 <= seconds < 86400:
            return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
        raise BadValue(f"{value!r} is not a time of day")
    m = _TIME_TEXT.match(str(value))
    if m:
        h, mi, s = int(m[1]), int(m[2]), int(m[3] or 0)
        if h < 24 and mi < 60 and s < 60:
            return f"{h:02d}:{mi:02d}:{s:02d}"
    raise BadValue(f"{value!r} is not a time of day")


def as_float(value) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(Decimal(str(value).strip()))
    except (InvalidOperation, ValueError):
        raise BadValue(f"{value!r} is not a number") from None


def as_trimmed(value) -> str | None:
    """Leading and trailing spaces removed; blank -> None."""
    if value is None:
        return None
    return str(value).strip() or None


def lane_config(value) -> str:
    """A LaneConfig as the speed db keeps it: digits 0-7, at most seven; anything else -> ''."""
    text = (as_trimmed(value) or "")
    return text if re.fullmatch(r"[0-7]{1,7}", text) else ""


# ------------------------------------------------------------------ the answer

@dataclass
class Site:
    """What QC Hub said about one sitenumber, in the speed db's shapes."""
    sitenumber: int
    lookup: str | None                       # legacycounts | orderlocation | not_found | None
    street_ns: str | None = None
    street_ew: str | None = None
    location_name: str | None = None         # the one name of a tube/survey site
    city: str | None = None
    state: str | None = None
    lane_configs: dict = field(default_factory=lambda: {d: "" for d in DIRECTIONS})
    control_type: str | None = None
    stop_controlled: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    count_date: str | None = None            # 'YYYY-MM-DD', the first scheduled day
    start_time: str | None = None            # 'HH:MM:SS'
    duration_hr: float | None = None
    notes: list = field(default_factory=list)
    message: str = ""                        # why the lookup is not checked (lookup None)

    @property
    def checked(self) -> bool:
        return self.lookup is not None

    @property
    def found(self) -> bool:
        return self.lookup in (LEGACYCOUNTS, ORDERLOCATION)

    @property
    def name(self) -> str:
        """'NS  -  EW' when either street is set, else the one location name, else ''."""
        return display_name(self.street_ns, self.street_ew, self.location_name)

    @property
    def has_lane_configs(self) -> bool:
        return any(self.lane_configs.values())

    def describe(self) -> str:
        if self.lookup == LEGACYCOUNTS:
            return f"in QC Hub as a turn movement site: {self.name}"
        if self.lookup == ORDERLOCATION:
            return f"in QC Hub as a tube/survey site: {self.name or '(no name)'}"
        if self.lookup == NOT_FOUND:
            return "not in QC Hub"
        return f"not checked ({self.message or 'QC Hub unavailable'})"


def display_name(street_ns: str | None, street_ew: str | None, location_name: str | None) -> str:
    if street_ns or street_ew:
        return f"{street_ns or ''}{NAME_SEPARATOR}{street_ew or ''}"
    return location_name or ""


def _hours(start: str | None, end: str | None) -> float | None:
    """EndTime - StartTime in hours; an end at or before the start is the next day."""
    if not start or not end:
        return None
    def seconds(t: str) -> int:
        h, m, s = (int(x) for x in t.split(":"))
        return h * 3600 + m * 60 + s
    span = seconds(end) - seconds(start)
    if span <= 0:
        span += 86400
    return round(span / 3600, 2)


def count_window(rows: list[dict], notes: list) -> tuple[str | None, str | None, float | None]:
    """(first scheduled date, its start time, duration in hours) from the legacycounts rows."""
    days = []
    for row in rows:
        try:
            day = as_date(row.get("DateScheduledForCount"))
            start, end = as_time(row.get("StartTime")), as_time(row.get("EndTime"))
        except BadValue as exc:
            notes.append(f"legacycounts: {exc}")
            continue
        if day:
            days.append((day, start, end))
    if not days:
        return None, None, None
    days.sort()
    first_day, start, end = days[0]
    last_day, _, last_end = days[-1]
    one_day = _hours(start, end)
    if first_day == last_day or one_day is None:
        return first_day, start, one_day
    gap = (date.fromisoformat(last_day) - date.fromisoformat(first_day)).days
    last = _hours(start, last_end) or one_day
    notes.append(f"{len(set(d for d, _, _ in days))} scheduled dates in QC Hub "
                 f"({first_day} to {last_day}); the window runs across all of them")
    return first_day, start, round(gap * 24 + last, 2)


def _coordinates(locations: list[dict], notes: list) -> tuple[float | None, float | None]:
    """The first location, by OrderLocationID, that has both coordinates."""
    found = []
    for loc in locations:
        try:
            lat, lon = as_float(loc.get("Latitude")), as_float(loc.get("Longitude"))
        except BadValue as exc:
            notes.append(f"orderlocation: {exc}")
            continue
        if lat is not None and lon is not None and (lat, lon) not in found:
            found.append((lat, lon))
    if len(found) > 1:
        notes.append(f"{len(found)} different lat/long pairs in orderlocation; the first by "
                     "OrderLocationID was used")
    return found[0] if found else (None, None)


def _street_name(qch: connection.Connection, location_id, notes: list, limit: float) -> str | None:
    """orderlocationstreet.StreetName for one location, or None with a note saying why."""
    try:
        rows = qch.rows(SITE_STREET_SQL, (location_id,), timeout=limit)
    except connection.QchUnavailable as exc:
        if exc.reason != connection.QUERY_FAILED:
            raise
        notes.append("QC Hub refused the street-name query (orderlocationstreet); the name was "
                     "left blank - type it in Setup")
        log.warning("orderlocationstreet query failed for OrderLocationID %s: %s", location_id, exc)
        return None
    main = [as_trimmed(r.get("StreetName")) for r in rows
            if str(r.get("StreetDirectionCode") or "").strip().upper() == "MAIN"]
    main = [n for n in main if n]
    if main:
        return main[0]
    names = []
    for r in rows:
        name = as_trimmed(r.get("StreetName"))
        if name and name not in names:
            names.append(name)
    if len(names) > 1:
        notes.append(f"{len(names)} street names in QC Hub for this location and none marked "
                     f"MAIN; joined with '{STREET_JOINER.strip()}'")
    return STREET_JOINER.join(names) if names else None


def lookup_site(qch: connection.Connection, sitenumber: int) -> Site:
    """One sitenumber, the two steps. Raises connection.QchUnavailable when QC Hub can't answer."""
    limit = config.QCH_PULL_TIMEOUT_S
    started = _time.monotonic()
    notes: list = []
    counts = qch.rows(SITE_LEGACYCOUNTS_SQL, (sitenumber,), timeout=limit)
    locations = qch.rows(SITE_LOCATION_SQL, (sitenumber,), timeout=limit)

    if counts:
        raw = counts[0]                               # the latest scheduled date
        lat, lon = _coordinates(locations, notes)
        day, start, hours = count_window(counts, notes)
        found = Site(
            sitenumber, LEGACYCOUNTS,
            street_ns=as_trimmed(raw.get("StreetNameNorthSouth")),
            street_ew=as_trimmed(raw.get("StreetNameEastWest")),
            city=as_trimmed(raw.get("City")), state=as_trimmed(raw.get("State")),
            lane_configs={d: lane_config(raw.get(f"{d}LaneConfig")) for d in DIRECTIONS},
            control_type=as_trimmed(raw.get("ControlType")),
            stop_controlled=as_trimmed(raw.get("StopControlled")),
            latitude=lat, longitude=lon, count_date=day, start_time=start, duration_hr=hours,
            notes=notes)
        if not found.has_lane_configs:
            notes.append("QC Hub has no lane configs for this site; four approaches are assumed")
    elif locations:
        first = locations[0]                          # lowest OrderLocationID
        lat, lon = _coordinates(locations, notes)
        if len(locations) > 1:
            notes.append(f"{len(locations)} locations in QC Hub for this sitenumber; the first by "
                         "OrderLocationID was used")
        found = Site(
            sitenumber, ORDERLOCATION,
            location_name=_street_name(qch, first.get("OrderLocationID"), notes, limit),
            city=as_trimmed(first.get("City")), state=as_trimmed(first.get("StateCode")),
            latitude=lat, longitude=lon, notes=notes)
    else:
        found = Site(sitenumber, NOT_FOUND, notes=notes)
    log.info("QC Hub lookup %s: %s (%.2f s)", sitenumber, found.describe(),
             _time.monotonic() - started)
    return found


def lookup_sites(sitenumbers: list[int],
                 qch: connection.Connection | None = None) -> dict[int, Site]:
    """Every sitenumber, on one connection. Never raises: QC Hub down = every site not checked."""
    out: dict[int, Site] = {}
    if not sitenumbers:
        return out
    try:
        if qch is not None:
            for n in sitenumbers:
                out[n] = lookup_site(qch, n)
        else:
            with connection.connected() as opened:
                for n in sitenumbers:
                    out[n] = lookup_site(opened, n)
    except connection.QchUnavailable as exc:
        log.warning("QC Hub lookup stopped after %d of %d sitenumbers: %s", len(out),
                    len(sitenumbers), exc)
        for n in sitenumbers:
            if n not in out:
                out[n] = Site(n, None, message=str(exc))
    return out
