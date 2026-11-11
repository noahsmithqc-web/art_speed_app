"""SP-3 parity: the engine against the six delivered workbooks (17650202 and 174083).

Three statements, each proved for all fifteen delivered directions:

  A. Fed the delivered records, the engine gives the delivered 85th percentile and % over posted
     exactly, and the delivered hourly 85th and 95th for every hour.
  B. Fed the raw Datalens exports through the real import and the delivered decisions (source
     cam, the manual floor, no adjustment), the tool gives the same row count and the same
     numbers exactly - so the import, the storage, the filter order and the stats all agree with
     the manual process.
  C. With the tool's default floor (below 5 mph) instead of the manual one, every 85th
     percentile is unchanged and the % over posted moves by a known, small amount. Those are the
     documented manual deviations (docs/PARITY.md), not errors.
"""

from __future__ import annotations

import math

import pytest

from art_speed import engine, loader, parity, project
from art_speed.importer import run
from art_speed.storage import db

CASES = sorted(parity.DELIVERED)


@pytest.fixture(scope="module")
def delivered(samples):
    books = {}
    for path in parity.delivered_files(samples):
        book = parity.read_delivered(path)
        books[book.sitenumber] = book
    assert sorted(books) == ["17408301", "17408302", "17408304", "17408305", "17408306", "17650202"]
    return books


@pytest.fixture(scope="module")
def imported(samples, tmp_path_factory):
    """The real exports through the real import, once."""
    folder = tmp_path_factory.mktemp("parity")
    proj = project.open_folder(folder, "174083")
    report = run.import_folder(proj, samples / "raw datalens speed output", use_qch=False)
    assert report.status == run.IMPORTED
    conn = db.open_db(proj.folder, proj.number)
    yield conn, {str(s.sitenumber): s.location_id for s in report.sites}
    conn.close()


def decide(conn, location_id, sitenumber, floors):
    """The delivered decisions for one location; `floors` maps direction -> mask_low."""
    with db.transaction(conn):
        for d in parity.DIRECTIONS:
            cam, _ = parity.DELIVERED.get((sitenumber, d), (None, None))
            conn.execute(
                "UPDATE speed_direction SET include_in_report = ?, posted_mph = ?, source_sitenumber = ?, "
                "source_cam = ?, adjust_by = 0, mask_low = ?, mask_high = NULL WHERE location_id = ? "
                "AND direction = ?",
                (int(cam is not None), 25 if cam else None, int(sitenumber) if cam else None, cam,
                 floors.get(d), location_id, d))


def test_every_delivered_direction_is_covered(delivered):
    have = sorted((sn, d) for sn, book in delivered.items() for d, item in book.directions.items() if item.rows)
    assert have == CASES


# ------------------------------------------------------------------ A

@pytest.mark.parametrize("sitenumber, direction", CASES)
def test_a_engine_on_the_delivered_records(delivered, sitenumber, direction):
    item = delivered[sitenumber].directions[direction]
    hist: dict = {}
    for speed in item.speeds:
        hist[speed] = hist.get(speed, 0) + 1
    got = engine.stats(hist, item.posted)
    assert got["p85"] == item.p85
    assert math.isclose(got["pct_over_posted"], item.pct_over_posted, abs_tol=1e-12)
    by_hour: dict = {}
    for row in item.rows:
        hour = by_hour.setdefault(int(row[6]), {})
        hour[row[4]] = hour.get(row[4], 0) + 1
        assert row[6] == row[1][:2]                   # the sheet's helper hour is the row's own hour
    for hour in range(24):
        want85, want95 = item.hourly_p85[hour], item.hourly_p95[hour]
        got85 = engine.percentile_inc(by_hour.get(hour, {}), 0.85) or 0      # the sheet prints 0 for an empty hour
        got95 = engine.percentile_inc(by_hour.get(hour, {}), 0.95) or 0
        assert math.isclose(got85, want85, abs_tol=1e-9), f"hour {hour:02d} 85th"
        assert math.isclose(got95, want95, abs_tol=1e-9), f"hour {hour:02d} 95th"


# ------------------------------------------------------------------ B

@pytest.mark.parametrize("sitenumber", sorted({sn for sn, _ in CASES}))
def test_b_raw_exports_with_the_delivered_decisions(delivered, imported, sitenumber):
    conn, locations = imported
    book = delivered[sitenumber]
    floors = {d: parity.DELIVERED[(sitenumber, d)][1] for d in parity.DIRECTIONS
              if (sitenumber, d) in parity.DELIVERED}
    decide(conn, locations[sitenumber], sitenumber, floors)
    drawer = loader.drawer(conn, locations[sitenumber])
    for d in parity.DIRECTIONS:
        item, block = book.directions[d], drawer["directions"][d]
        if (sitenumber, d) not in parity.DELIVERED:
            assert block["report"] is None and not item.rows
            continue
        report = block["report"]
        assert report["n"] == len(item.rows), f"{d} row count"
        assert report["p85"] == item.p85, f"{d} 85th"
        assert math.isclose(report["pct_over_posted"], item.pct_over_posted, abs_tol=1e-12), f"{d} % over"
        assert report["adjust_by"] == 0 and report["source_cam"] == f"{sitenumber}:{parity.DELIVERED[(sitenumber, d)][0]}"
        (day,) = drawer["hourly"]["directions"][d]["days"]
        for hour, cell in enumerate(day["cells"]):
            got85 = (cell["p85"] if cell and cell["n"] else 0) or 0
            got95 = (cell["p95"] if cell and cell["n"] else 0) or 0
            assert math.isclose(got85, item.hourly_p85[hour], abs_tol=1e-9), f"{d} hour {hour:02d} 85th"
            assert math.isclose(got95, item.hourly_p95[hour], abs_tol=1e-9), f"{d} hour {hour:02d} 95th"
        assert (day["n"], day["p85"]) == (report["n"], report["p85"])       # one count day: day = count


# ------------------------------------------------------------------ C

@pytest.mark.parametrize("sitenumber", sorted({sn for sn, _ in CASES}))
def test_c_default_floor_and_the_documented_deviations(delivered, imported, sitenumber):
    conn, locations = imported
    book = delivered[sitenumber]
    decide(conn, locations[sitenumber], sitenumber, {d: 5 for d in parity.DIRECTIONS})
    drawer = loader.drawer(conn, locations[sitenumber])
    for d in parity.DIRECTIONS:
        if (sitenumber, d) not in parity.DELIVERED:
            continue
        item, report = book.directions[d], drawer["directions"][d]["report"]
        n, pct = parity.DEFAULT_FLOOR[(sitenumber, d)]
        assert drawer["directions"][d]["mask"]["is_default"]
        assert report["p85"] == item.p85, f"{d}: the 85th percentile does not move with the 5 mph floor"
        assert report["n"] == n
        assert math.isclose(report["pct_over_posted"], pct, abs_tol=5e-7)
        shift = report["pct_over_posted"] - item.pct_over_posted
        assert 0 <= shift <= parity.MAX_SHIFT, f"{d}: % over posted moved by {shift:.4%}"
        assert report["n"] <= len(item.rows) or parity.DELIVERED[(sitenumber, d)][1] is not None
