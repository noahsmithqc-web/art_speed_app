"""The speed engine: rows in, numbers out (plan section 6, SP-3).

Pure functions. No database, no files, no UI - everything here takes plain values and returns
plain values, so the same code can sit behind the drawer, the report parity test, the CLI dump
and, later, a per-day or time-of-day report.

Every statistic is computed over a **filtered row set**. The filter is, in this order:

    1. slice window      the location's slices for the cam's sitenumber (optional date, optional
                         from-to hour)
    2. time masks        masked date-hours of the cam are removed
    3. zeros             a speed of 0 is never a speed
    4. lower mask        `low` = 5 means "below 5 mph is masked": 5 stays, 4 goes
    5. upper mask        `high` = 60 means "above 60 mph is masked"
    6. adjust_by         added to every speed that is left
    7. stats             n, average, 85th and 95th (PERCENTILE.INC), % over posted (strictly greater)

The row set is held as hourly 1-mph bin counts - {'YYYY-MM-DD HH': {speed: rows}} per direction -
which is what `bin_rows()` makes from raw rows. Slices and time masks are whole hours, so the
bins lose nothing: every statistic below over the bins equals the same statistic over the rows
(a percentile over bin counts is exact). When minute-level windows arrive, the rows are filtered
first and binned after; nothing here changes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

DIRECTIONS = ("NB", "SB", "EB", "WB")
# The corner a direction's speed gate is best seen from, then the second best.
PRIMARY = {"NB": ("NE", "SE"), "SB": ("SW", "NW"), "EB": ("SE", "SW"), "WB": ("NW", "NE")}
PRIMARY_ROLE, SECONDARY_ROLE = "Primary", "Secondary"


# ------------------------------------------------------------------ small things

def js_round(x: float) -> int:
    """Whole mph the way the page rounds it: half up (27.5 -> 28), never banker's."""
    return math.floor(x + 0.5)


def hour_key(day: str, clock: str) -> str:
    """('2026-02-24', '06:00:12') -> '2026-02-24 06'."""
    return f"{day} {clock[:2]}"


def bin_rows(rows) -> dict:
    """Raw rows -> {direction: {'YYYY-MM-DD HH': {speed: rows}}}.

    rows: any iterable of (date 'YYYY-MM-DD', time 'HH:MM:SS', direction, speed).
    """
    out: dict = {}
    for day, clock, direction, speed in rows:
        hour = out.setdefault(direction, {}).setdefault(hour_key(day, clock), {})
        hour[speed] = hour.get(speed, 0) + 1
    return out


def slice_has(s: dict, key: str) -> bool:
    """Is the date-hour inside the slice? A slice may fix a date, an hour window, both or neither."""
    if s.get("date") and s["date"] != key[:10]:
        return False
    if s.get("from_hour") is not None:
        hour = int(key[11:13])
        if hour < s["from_hour"] or hour >= s["to_hour"]:
            return False
    return True


def in_slices(slices: list, key: str) -> bool:
    return any(slice_has(s, key) for s in slices)


def percentile_inc(hist: dict, p: float = 0.85) -> float | None:
    """Excel's PERCENTILE.INC over {value: count}. None for an empty set.

    Rank q = (n - 1) * p into the sorted values, linear between the two neighbours - the same
    arithmetic, in the same order, as the mockup's and Excel's, so the floats agree bit for bit.
    """
    n = sum(hist.values())
    if not n:
        return None
    q = (n - 1) * p
    f, c = math.floor(q), math.ceil(q)
    lo = hi = None
    seen = 0
    for value in sorted(hist):
        seen += hist[value]
        if lo is None and f < seen:
            lo = value
        if c < seen:
            hi = value
            break
    return lo if f == c else lo + (hi - lo) * (q - f)


def mean(hist: dict) -> float | None:
    n = sum(hist.values())
    return sum(v * c for v, c in hist.items()) / n if n else None


def role(corner: str | None, direction: str) -> str:
    """'Primary' / 'Secondary' / '' - the cam's angle on a direction, from its corner."""
    if not corner:
        return ""
    first, second = PRIMARY[direction]
    return PRIMARY_ROLE if corner == first else SECONDARY_ROLE if corner == second else ""


# ------------------------------------------------------------------ steps 1-3: the observed set

@dataclass
class Observed:
    """One cam x direction after the slice window and the time masks: zeros counted apart."""
    hist: dict = field(default_factory=dict)     # {speed: rows}, non-zero speeds only
    zero: int = 0
    n: int = 0                                   # every row that is left, zeros included
    masked_hours: int = 0                        # date-hours inside the slices that are time-masked

    @property
    def n_nonzero(self) -> int:
        return self.n - self.zero


def observe(bins: dict, slices: list, masked=()) -> Observed:
    """bins: {'YYYY-MM-DD HH': {speed: rows}} of one cam x direction.
    slices: the location's slices for that cam's sitenumber. masked: the cam's masked date-hours."""
    out = Observed()
    masked = set(masked)
    for key, counts in bins.items():
        if not in_slices(slices, key):
            continue
        if key in masked:
            out.masked_hours += 1
            continue
        for speed, rows in counts.items():
            if speed == 0:
                out.zero += rows
            else:
                out.hist[speed] = out.hist.get(speed, 0) + rows
            out.n += rows
    return out


def raw_stats(obs: Observed) -> dict:
    """The QC numbers per cam x direction, before any speed mask: % zero is always shown."""
    return {"n": obs.n, "zero": obs.zero, "n_nonzero": obs.n_nonzero,
            "pct_zero": obs.zero / obs.n if obs.n else 0,
            "avg": mean(obs.hist), "p85": percentile_inc(obs.hist)}


# ------------------------------------------------------------------ steps 4-7: the report values

def apply_masks(hist: dict, low: int | None, high: int | None, adjust_by: int = 0) -> tuple[dict, int]:
    """({adjusted speed: rows} of what is kept, rows removed by the two handles)."""
    kept: dict = {}
    removed = 0
    for speed, rows in hist.items():
        if (low is not None and speed < low) or (high is not None and speed > high):
            removed += rows
        else:
            kept[speed + adjust_by] = kept.get(speed + adjust_by, 0) + rows
    return kept, removed


def stats(kept: dict, posted: float | None = None) -> dict:
    """n, average, 85th, 95th and % over posted of a masked, adjusted set."""
    n = sum(kept.values())
    over = sum(c for v, c in kept.items() if v > posted) if posted else None
    return {"n": n, "avg": mean(kept), "p85": percentile_inc(kept, 0.85),
            "p95": percentile_inc(kept, 0.95), "over_posted": over,
            "pct_over_posted": over / n if over is not None and n else None}


def final_stats(obs: Observed, *, low: int | None, high: int | None, adjust_by: int = 0,
                posted: float | None = None) -> dict:
    """The report values of one direction from its source cam's observed set."""
    kept, removed = apply_masks(obs.hist, low, high, adjust_by)
    out = stats(kept, posted)
    out.update({"events": obs.n, "zero": obs.zero, "pct_zero": obs.zero / obs.n if obs.n else 0,
                "masked": removed, "pct_masked": removed / obs.n_nonzero if obs.n_nonzero else 0,
                "masked_hours": obs.masked_hours})
    return out


def hour_stats(counts: dict, *, low: int | None, high: int | None, adjust_by: int = 0) -> dict:
    """One date-hour of a cam x direction: rows, zeros, and the masked, adjusted n / 85th / 95th.
    The time mask is not applied here - a masked hour still shows its struck-through value."""
    zero = counts.get(0, 0)
    kept, _ = apply_masks({s: c for s, c in counts.items() if s != 0}, low, high, adjust_by)
    return {"rows": sum(counts.values()), "zero": zero, "n": sum(kept.values()),
            "p85": percentile_inc(kept, 0.85), "p95": percentile_inc(kept, 0.95), "kept": kept}


def bins10(hist: dict, upper_mask: int | None = None) -> list:
    """Vehicles per 10-mph bin from the lowest to the highest bin that occurs, zeros included.
    A bin wholly above the upper mask is flagged."""
    if not hist:
        return []
    out = []
    for start in range(min(hist) // 10 * 10, max(hist) + 1, 10):
        out.append({"from": start, "to": start + 9,
                    "n": sum(c for v, c in hist.items() if start <= v < start + 10),
                    "above_upper_mask": upper_mask is not None and start > upper_mask})
    return out


# ------------------------------------------------------------------ the source-cam suggestion

def suggest(candidates: list) -> dict | None:
    """Plan section 6: primary angle if one exists, else secondary, else the lowest % zero;
    % zero breaks ties among several primaries / secondaries.

    candidates: [{'cam': key, 'role': 'Primary'|'Secondary'|'', 'pct_zero': float,
                  'rows': int, 'usable': bool}] in cam order. `usable` is False for a dead cam
    or one with no non-zero speed in the direction; such a cam is skipped and the reason says so.
    """
    with_rows = [c for c in candidates if c["rows"] > 0]
    usable = [c for c in with_rows if c["usable"]]
    if not usable:
        return None
    rank = {PRIMARY_ROLE: 0, SECONDARY_ROLE: 1}
    order = sorted(range(len(usable)), key=lambda i: (rank.get(usable[i]["role"], 2),
                                                      usable[i]["pct_zero"], i))
    best = usable[order[0]]
    best_rank = rank.get(best["role"], 2)
    same = sum(1 for c in usable if rank.get(c["role"], 2) == best_rank)
    why = ("primary corner", "no primary · secondary corner", "no on-axis cam · lowest % zero")[best_rank]
    if same > 1 and best_rank < 2:
        why += f" · lowest % zero of {same}"
    if len(usable) < len(with_rows):
        why += " · dead cam skipped"
    return {"cam": best["cam"], "why": why}
