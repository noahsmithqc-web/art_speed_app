"""state -> nm_region, from the table that ships inside the app.

The CSV is `art_speed/reference/state_nm_region.csv`: 51 rows (50 states + DC) over five
regions. It ships as a file rather than a table in the code so the regions can be changed
without a new build. A state that isn't in the file gets a blank region (Q6) - the state
itself is still stored.

`nm_region` is what gets stored; `nm_region_display` is only what a screen shows (Q48), so
"midatlantic_northeast" goes in the project file and "Mid-Atlantic / Northeast" goes on
Summary. A region with no display text falls back to showing the stored value.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache

from art_speed import paths
from art_speed.log import get_logger

log = get_logger(__name__)

CSV_NAME = "state_nm_region.csv"
_TWO_LETTERS = re.compile(r"[A-Za-z]{2}")


class InvalidState(ValueError):
    """The text is not a two-letter state code."""


def clean_state(value: str | None) -> str | None:
    """Two letters, uppercase. Blank (or None) means 'no state' and returns None.

    Raises InvalidState for anything else, so a typo is refused rather than stored.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not _TWO_LETTERS.fullmatch(text):
        raise InvalidState(f"{value!r} is not a two-letter state code (for example VA).")
    return text.upper()


@lru_cache(maxsize=1)
def table() -> dict:
    """{'VA': 'midatlantic_northeast', ...}. Read once, then kept in memory."""
    path = paths.resource_path("reference", CSV_NAME)
    found: dict = {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                state = (row.get("state") or "").strip().upper()
                region = (row.get("nm_region") or "").strip()
                if state and region:
                    found[state] = region
    except OSError as exc:
        log.error("region table %s could not be read (%s); every region will be blank", path, exc)
        return {}
    if not found:
        log.error("region table %s is empty; every region will be blank", path)
    return found


@lru_cache(maxsize=1)
def labels() -> dict:
    """{'midatlantic_northeast': 'Mid-Atlantic / Northeast', ...}, from the same file (Q48)."""
    path = paths.resource_path("reference", CSV_NAME)
    found: dict = {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                region = (row.get("nm_region") or "").strip()
                label = (row.get("nm_region_display") or "").strip()
                if not region or not label:
                    continue
                if found.setdefault(region, label) != label:
                    log.warning("%s gives %s two display names (%r and %r); using the first",
                                CSV_NAME, region, found[region], label)
    except OSError as exc:
        log.error("region table %s could not be read (%s); regions show as stored", path, exc)
        return {}
    return found


def label_for(region: str | None) -> str | None:
    """What a screen shows for a stored nm_region. The stored value itself when the CSV has no
    display text for it, and None for no region at all."""
    if not region:
        return None
    return labels().get(region, region)


def region_for(state: str | None) -> str | None:
    """The nm_region for a state code, or None when the state is blank or unknown."""
    code = clean_state(state)
    if code is None:
        return None
    region = table().get(code)
    if region is None:
        log.warning("state %s is not in %s; nm_region left blank", code, CSV_NAME)
    return region


def regions() -> list:
    """Every region name in the table, sorted. Used by the tests and the walkthrough."""
    return sorted(set(table().values()))
