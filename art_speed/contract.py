"""The JSON contract between the engine/API and the page (SP-0).

`speed_fixtures.json` was extracted from the locked mockup (speed_tab_mock_v15.html) and is the
spec: the engine's dump (SP-3) and the API's responses (SP-4) have exactly these fields.
This module is that spec in code, so a test can hold any payload against it:

    errors = contract.check_fixtures(data)     # the whole file: project, tab, every drawer
    errors = contract.check_drawer(payload)    # GET /speed/locations/{id}
    errors = contract.check_tab(payload)       # GET /speed/locations

An empty list means the payload has every field, no extra ones, and each of the right kind.
docs/CONTRACT.md describes what each field means.
"""

from __future__ import annotations

CONTRACT_VERSION = 1

DIRECTIONS = ("NB", "SB", "EB", "WB")
AXES = {"NS": ("NB", "SB"), "EW": ("EB", "WB")}
CORNERS = ("NW", "NE", "SW", "SE")
# The corner a direction's speed gate is best seen from, then the second best (QA template).
PRIMARY = {"NB": ("NE", "SE"), "SB": ("SW", "NW"), "EB": ("SE", "SW"), "WB": ("NW", "NE")}
PEAK_HOURS = (7, 8, 16, 17)          # SP-Q20: hardcoded
MASK_LOW_DEFAULT = 5                 # "below 5 mph is masked": 5 shows, 4 does not
THIN_BELOW = 30                      # Detail: an hour with fewer speeds is drawn grey
BLANK_BELOW = 5                      # ... and with fewer than this, blank

READY, NEEDS_INPUT = "ready", "needs_input"
STATUS_LABELS = {READY: "Ready", NEEDS_INPUT: "Needs input"}
WARNING_CODES = ("dead_cam", "off_leg", "outside_window", "over_half_masked", "header_vs_qch",
                 "not_in_qch")
CSV_FORMS = ("raw", "post-macro")
WINDOW_SOURCES = ("QC Hub", "file header", "edited", "slice", "rows")
ROLES = ("Primary", "Secondary", "")


# ------------------------------------------------------------------ a small schema language
# A schema is: a type (str, int, bool), NUM (int or float, never bool), a dict of exact keys,
# or one of the wrappers below.

class _Num:
    def __repr__(self) -> str:
        return "number"


NUM = _Num()


def opt(schema):            # the value, or None
    return ("opt", schema)


def many(schema):           # a list of them
    return ("list", schema)


def keyed(schema):          # a dict whose keys are data (cam keys, date-hours, speeds)
    return ("map", schema)


def one_of(*values):
    return ("enum", values)


def per_direction(schema):  # exactly NB, SB, EB, WB
    return {d: schema for d in DIRECTIONS}


SLICE = {"sitenumber": str, "date": opt(str), "from_hour": opt(int), "to_hour": opt(int), "label": str}
WINDOW = {"start_date": opt(str), "start_time": opt(str), "duration_hours": opt(NUM),
          "source": one_of(*WINDOW_SOURCES)}
STATUS = {"state": one_of(READY, NEEDS_INPUT), "label": str, "missing": many(str)}
WARNING = {"code": one_of(*WARNING_CODES), "text": str}          # over_half_masked adds two keys
WARNING_ACK = dict(WARNING, cam=str, can_acknowledge=bool)

REPORT = {"source_cam": str, "adjust_by": int, "events": int, "zero": int, "pct_zero": NUM,
          "masked": int, "pct_masked": NUM, "n": int, "avg": opt(NUM), "p85": opt(NUM),
          "p95": opt(NUM), "over_posted": opt(int), "pct_over_posted": opt(NUM), "masked_hours": int}
DIRECTION = {"exists": bool, "include_in_report": bool, "posted": opt(NUM), "lane_config": str,
             "lane_config_qch": str, "source_cam": opt(str),
             "suggested": opt({"cam": str, "why": str}), "adjust_by": int,
             "mask": {"low": opt(int), "high": opt(int), "is_default": bool},
             "report": opt(REPORT), "off_leg_events": int}
CAM_DIRECTION = {"n": int, "zero": int, "n_nonzero": int, "pct_zero": NUM, "avg": opt(NUM),
                 "p85": opt(NUM), "role": one_of(*ROLES)}
CAM = {"key": str, "sitenumber": str, "cam": int, "label": str, "corner": opt(one_of(*CORNERS)),
       "file": str, "csv_form": one_of(*CSV_FORMS), "events": int, "events_in_location": int,
       "dead": bool, "masked_hours": many(str), "mask_acknowledged": bool, "hours_with_data": int,
       "over_half_masked": bool, "directions": per_direction(CAM_DIRECTION)}
HISTOGRAM = {"events": int, "zero": int, "masked_hours": int, "bins": keyed(int),
             "occupied": many(int), "min_speed": opt(int), "max_speed": opt(int),
             "bins10": many({"from": int, "to": int, "n": int, "above_upper_mask": bool})}
HOUR_CELL = {"hour": int, "rows": int, "zero": int, "n": int, "p85": opt(NUM), "p95": opt(NUM),
             "masked": bool, "peak": bool}
HOURLY_DIRECTION = {"source_cam": str, "masked_hours": int, "shared_with": many(str),
                    "days": many({"date": str, "n": int, "p85": opt(NUM), "p95": opt(NUM),
                                  "cells": many(opt(HOUR_CELL))})}
HOURLY = {"dates": many(str), "hours_with_data": int, "peak_hours": many(int), "scale_max": NUM,
          "thin_below": int, "blank_below": int, "directions": keyed(opt(HOURLY_DIRECTION))}
TRAIL = {"when": str, "what": str, "who": str, "scope": one_of("location", "site", "cam")}
TRAIL_SITE = dict(TRAIL, sitenumber=str)        # site and cam lines name their sitenumber
SITENUMBER = {"sitenumber": str, "sitecode": str, "name": str,
              "street_ns": opt(str), "street_ew": opt(str),
              "qch_lookup": opt(one_of("legacycounts", "orderlocation", "not_found", "manual")),
              "header": {"sitecode": opt(str), "start_date": opt(str), "start_time": opt(str),
                         "duration_hours": opt(NUM)}}
LOCATION = {"id": int, "code": str, "name": str, "name_source": one_of("qch", "manual"),
            "manual_name": opt(str), "qch_name": opt(str), "sitenumbers": many(SITENUMBER),
            "slices": many(SLICE), "is_sliced": bool, "is_grouped": bool,
            "latitude": opt(NUM), "longitude": opt(NUM), "geometry_source": one_of("qch", "manual"),
            "nm_grouping_used": bool, "corners_linked": bool, "window": WINDOW,
            "count_days": many(str), "cams": int, "events": int, "masked_hours": int}

DRAWER = {"location": LOCATION, "posted_same": {"NS": bool, "EW": bool},
          "directions": per_direction(DIRECTION), "cams": many(CAM),
          "histograms": keyed(keyed(HISTOGRAM)),            # cam key -> direction -> histogram
          "hourly": HOURLY,
          "bins": keyed(keyed(keyed(keyed(int)))),          # cam key -> direction -> 'YYYY-MM-DD HH' -> speed -> n
          "note": str, "status": STATUS, "warnings": many(("warning",)),
          "paper_trail": many(("trail",))}

COUNT = {"set": int, "of": int}
TAB_ROW = {"id": int, "code": str, "name": str, "sitenumbers": many(str), "slices": many(SLICE),
           "is_sliced": bool, "is_grouped": bool, "window": WINDOW, "cams": int,
           "in_report": many(one_of(*DIRECTIONS)), "events": int, "corners": COUNT, "posted": COUNT,
           "source_cams": COUNT, "status": STATUS, "warnings": many(str)}
TAB = {"summary": {"sitenumbers": int, "locations": int, "cams": int, "last_import": opt(str),
                   "qch_snapshot": opt(str), "last_report": opt(str)},
       "generate": {"enabled": bool, "green": bool}, "rows": many(TAB_ROW)}
PROJECT = {"number": str, "name": opt(str), "state": opt(str), "client": opt(str), "db": str,
           "is_art_project": bool}


def _check(value, schema, path: str, errors: list) -> None:
    if schema is NUM:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}: expected a number, got {value!r}")
    elif schema in (str, int, bool):
        if not isinstance(value, schema) or (schema is int and isinstance(value, bool)):
            errors.append(f"{path}: expected {schema.__name__}, got {value!r}")
    elif isinstance(schema, dict):
        if not isinstance(value, dict):
            errors.append(f"{path}: expected an object, got {type(value).__name__}")
            return
        for key in schema:
            if key not in value:
                errors.append(f"{path}.{key}: missing")
        for key in value:
            if key not in schema:
                errors.append(f"{path}.{key}: not in the contract")
        for key, sub in schema.items():
            if key in value:
                _check(value[key], sub, f"{path}.{key}", errors)
    elif schema[0] == "opt":
        if value is not None:
            _check(value, schema[1], path, errors)
    elif schema[0] == "list":
        if not isinstance(value, list):
            errors.append(f"{path}: expected a list, got {type(value).__name__}")
            return
        for i, item in enumerate(value):
            _check(item, schema[1], f"{path}[{i}]", errors)
    elif schema[0] == "map":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected an object, got {type(value).__name__}")
            return
        for key, item in value.items():
            _check(item, schema[1], f"{path}[{key!r}]", errors)
    elif schema[0] == "enum":
        if value not in schema[1]:
            errors.append(f"{path}: {value!r} is not one of {', '.join(map(repr, schema[1]))}")
    elif schema[0] == "warning":
        ack = isinstance(value, dict) and value.get("code") == "over_half_masked"
        _check(value, WARNING_ACK if ack else WARNING, path, errors)
    elif schema[0] == "trail":
        shared = isinstance(value, dict) and value.get("scope") in ("site", "cam")
        _check(value, TRAIL_SITE if shared else TRAIL, path, errors)
    else:                                                   # pragma: no cover - a typo above
        raise ValueError(f"unknown schema node {schema!r}")


def check_drawer(payload, path: str = "drawer") -> list[str]:
    errors: list = []
    _check(payload, DRAWER, path, errors)
    if not errors:
        for day_path, cells in _hour_rows(payload, path):
            if len(cells) != 24:
                errors.append(f"{day_path}.cells: {len(cells)} cells, expected 24 (hours 00-23)")
    return errors


def _hour_rows(payload: dict, path: str):
    for d, block in payload["hourly"]["directions"].items():
        if block:
            for i, day in enumerate(block["days"]):
                yield f"{path}.hourly.directions[{d!r}].days[{i}]", day["cells"]


def check_tab(payload, path: str = "tab") -> list[str]:
    errors: list = []
    _check(payload, TAB, path, errors)
    return errors


def check_fixtures(data) -> list[str]:
    """The whole speed_fixtures.json: project, tab and one drawer per tab row."""
    errors: list = []
    if not isinstance(data, dict):
        return ["fixtures: not a JSON object"]
    if data.get("contract_version") != CONTRACT_VERSION:
        errors.append(f"contract_version is {data.get('contract_version')!r}, expected {CONTRACT_VERSION}")
    _check(data.get("project"), PROJECT, "project", errors)
    errors += check_tab(data.get("tab"))
    locations = data.get("locations")
    if not isinstance(locations, dict):
        return errors + ["locations: missing"]
    for key, payload in locations.items():
        errors += check_drawer(payload, f"locations[{key}]")
    rows = (data.get("tab") or {}).get("rows") or []
    ids = [str(r.get("id")) for r in rows if isinstance(r, dict)]
    if sorted(ids) != sorted(locations):
        errors.append(f"tab rows {ids} and drawers {sorted(locations)} are not the same locations")
    return errors
