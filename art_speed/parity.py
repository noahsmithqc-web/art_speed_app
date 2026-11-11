"""The delivered speed workbooks as parity targets (plan sections 6 and 7a, SP-Q23, SP-Q27).

The six workbooks in `sample reports\\final report` are what the manual process delivered. This
module reads one - its header, the records on each direction sheet, and the values Excel last
computed for the Summary and the hourly table - and lists the decisions the manual process made,
recovered by matching every delivered record to the raw Datalens exports (track id + time):

    DELIVERED[(sitenumber, direction)] = (source cam, manual lower floor)

The manual floor was not the same everywhere: twelve directions kept every non-zero speed and
three dropped speeds below 4 mph. The tool's default is "below 5 mph" for every direction. With
the delivered floor the engine reproduces every delivered number exactly; with the default floor
every 85th percentile is unchanged and the % over posted moves by at most 0.28 of a percentage
point. tests/test_parity.py holds both, and docs/PARITY.md shows the table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DIRECTIONS = ("NB", "SB", "EB", "WB")

# (sitenumber, direction) -> (source cam the delivered sheet's rows came from, manual lower floor)
DELIVERED = {
    ("17408301", "NB"): (2, None), ("17408301", "SB"): (3, None),
    ("17408302", "NB"): (3, None), ("17408302", "SB"): (3, None),
    ("17408304", "NB"): (2, 4), ("17408304", "SB"): (2, None),
    ("17408305", "NB"): (3, None), ("17408305", "SB"): (4, None), ("17408305", "EB"): (4, None),
    ("17408306", "NB"): (1, 4), ("17408306", "SB"): (1, 4),
    ("17650202", "NB"): (1, None), ("17650202", "SB"): (2, None),
    ("17650202", "EB"): (2, None), ("17650202", "WB"): (1, None),
}

# With the tool's default floor (below 5 mph masked): the 85th percentile of every direction is
# the delivered one; these are the % over posted that result, and the n behind them.
DEFAULT_FLOOR = {
    ("17408301", "NB"): (1759, 0.201819), ("17408301", "SB"): (1588, 0.227960),
    ("17408302", "NB"): (936, 0.342949), ("17408302", "SB"): (778, 0.419023),
    ("17408304", "NB"): (4770, 0.253459), ("17408304", "SB"): (4320, 0.174306),
    ("17408305", "NB"): (6843, 0.460178), ("17408305", "SB"): (6852, 0.221687),
    ("17408305", "EB"): (8, 0.0),
    ("17408306", "NB"): (3242, 0.001542), ("17408306", "SB"): (4177, 0.062964),
    ("17650202", "NB"): (1563, 0.001919), ("17650202", "SB"): (1391, 0.000719),
    ("17650202", "EB"): (61, 0.0), ("17650202", "WB"): (70, 0.0),
}
MAX_SHIFT = 0.0028          # the largest move in % over posted (as a fraction): 17408305 SB


@dataclass
class DeliveredDirection:
    direction: str
    hidden: bool
    posted: float | None
    p85: float | None                    # the sheet's own H5, as Excel last computed it
    pct_over_posted: float | None        # J2
    rows: list = field(default_factory=list)         # (date 'YYYY-MM-DD', time 'HH:MM:SS', direction text, class, speed, track id, hour 'HH')
    hourly_p85: dict = field(default_factory=dict)   # {hour: value}; 0 where the hour has no rows
    hourly_p95: dict = field(default_factory=dict)

    @property
    def speeds(self) -> list:
        return [r[4] for r in self.rows]


@dataclass
class DeliveredWorkbook:
    path: Path
    sitenumber: str
    location: str
    start_date: str | None
    start_time: str | None
    duration_hr: float | None
    directions: dict = field(default_factory=dict)   # {direction: DeliveredDirection}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def read_delivered(path: str | os.PathLike) -> DeliveredWorkbook:
    """Read a delivered workbook: the values Excel computed, not the formulas."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    summary, stats = wb["Summary"], wb["summary_stats"]
    start = summary["E4"].value
    clock = summary["E5"].value
    out = DeliveredWorkbook(
        path=Path(path), sitenumber=str(summary["E2"].value), location=str(summary["E3"].value or ""),
        start_date=start.date().isoformat() if hasattr(start, "date") else None,
        start_time=clock.strftime("%H:%M:%S") if hasattr(clock, "strftime") else None,
        duration_hr=_number(summary["E6"].value))
    for i, d in enumerate(DIRECTIONS):
        ws = wb[d]
        item = DeliveredDirection(d, ws.sheet_state != "visible", _number(ws["J1"].value),
                                  _number(ws["H5"].value), _number(ws["J2"].value))
        for row in ws.iter_rows(min_row=8, max_col=7, values_only=True):
            if _number(row[4]) is None:
                continue
            day = row[0].date().isoformat() if hasattr(row[0], "date") else str(row[0])
            clock = row[1].strftime("%H:%M:%S") if hasattr(row[1], "strftime") else str(row[1])
            item.rows.append((day, clock, row[2], row[3], row[4], row[5], row[6]))
        for r in range(6, 30):                       # summary_stats rows 6-29 are hours 00-23
            hour = r - 6
            item.hourly_p85[hour] = _number(stats.cell(r, 4 + i).value)
            item.hourly_p95[hour] = _number(stats.cell(r, 8 + i).value)
        out.directions[d] = item
    return out


def delivered_files(samples: str | os.PathLike) -> list:
    return sorted((Path(samples) / "final report").glob("*.xlsx"))
