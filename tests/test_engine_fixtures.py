"""SP-3: the engine rebuilds the mockup's numbers.

speed_fixtures.json was produced by the locked mockup's own script. Here the fixture's *inputs*
(slices, cams with their hourly bins, corners, masks, decisions) are fed to the Python engine and
the payload it builds must equal the fixture, field for field - every count exactly, every
float to 1e-9.
"""

from __future__ import annotations

import math

import pytest

from art_speed import contract, payload

LOCATIONS = ["1", "2", "3", "4", "5", "6"]


def differences(got, want, path="") -> list:
    """Where two JSON-like values differ. Numbers compare to 1e-9; everything else exactly."""
    if isinstance(want, bool) or isinstance(got, bool) or want is None or got is None:
        return [] if got is want or got == want and type(got) is type(want) else [f"{path}: {got!r} != {want!r}"]
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return [] if math.isclose(got, want, rel_tol=1e-9, abs_tol=1e-9) else [f"{path}: {got!r} != {want!r}"]
    if isinstance(want, dict) and isinstance(got, dict):
        out = [f"{path}.{k}: missing" for k in want if k not in got]
        out += [f"{path}.{k}: unexpected" for k in got if k not in want]
        for k in want:
            if k in got:
                out += differences(got[k], want[k], f"{path}.{k}")
        return out
    if isinstance(want, list) and isinstance(got, list):
        if len(got) != len(want):
            return [f"{path}: {len(got)} items != {len(want)}"]
        out = []
        for i, (g, w) in enumerate(zip(got, want)):
            out += differences(g, w, f"{path}[{i}]")
        return out
    return [] if got == want else [f"{path}: {got!r} != {want!r}"]


@pytest.mark.parametrize("key", LOCATIONS)
def test_engine_rebuilds_the_fixture_drawer(fixtures, key):
    want = fixtures["locations"][key]
    got = payload.build_drawer(payload.from_drawer(want))
    assert contract.check_drawer(got) == []
    diffs = differences(got, want)
    assert diffs == [], f"{len(diffs)} differences, first: {diffs[:8]}"


def test_engine_rebuilds_the_tab(fixtures):
    rows = [payload.tab_row(payload.build_drawer(payload.from_drawer(fixtures["locations"][str(r["id"])])))
            for r in fixtures["tab"]["rows"]]
    assert differences(rows, fixtures["tab"]["rows"]) == []
    assert payload.generate_state(rows) == fixtures["tab"]["generate"]


def test_the_weeklong_location_is_fast_enough(fixtures):
    import time
    data = payload.from_drawer(fixtures["locations"]["6"])          # 269,187 events, 7 days
    started = time.perf_counter()
    payload.build_drawer(data)
    assert time.perf_counter() - started < 5


# ------------------------------------------------------------------ the same numbers from the real files

def apply_fixture_decisions(conn, location_id, want):
    """Put a fixture drawer's inputs (lane configs, corners, decisions) on an imported location."""
    from art_speed.storage import db
    site = int(want["location"]["sitenumbers"][0]["sitenumber"])
    with db.transaction(conn):
        for cam in want["cams"]:
            conn.execute("UPDATE speed_cam SET corner = ? WHERE sitenumber = ? AND cam = ?",
                         (cam["corner"], site, cam["cam"]))
        for d, block in want["directions"].items():
            conn.execute("UPDATE speed_qch_site SET {}_lane_config = ? WHERE sitenumber = ?".format(d.lower()),
                         (block["lane_config_qch"], site))
            src = block["source_cam"]
            conn.execute(
                "UPDATE speed_direction SET include_in_report = ?, posted_mph = ?, source_sitenumber = ?, "
                "source_cam = ?, adjust_by = ?, mask_low = ?, mask_high = ? WHERE location_id = ? AND direction = ?",
                (int(block["include_in_report"]), block["posted"], site if src else None,
                 int(src.split(":")[1]) if src else None, block["adjust_by"], block["mask"]["low"],
                 block["mask"]["high"], location_id, d))


@pytest.mark.parametrize("key", ["1", "2", "3", "4", "5"])
def test_the_real_exports_through_the_import_give_the_mockup_numbers(fixtures, samples, tmp_path, key):
    """174083's five locations: raw CSVs -> import -> speed db -> SQL bins -> engine equals what the
    mockup computed from its baked-in bins, for every computed block of the drawer."""
    from art_speed import loader, project
    from art_speed.importer import run
    from art_speed.storage import db
    want = fixtures["locations"][key]
    site = want["location"]["sitenumbers"][0]["sitenumber"]
    folder = tmp_path / "csv"
    folder.mkdir()
    for path in (samples / "raw datalens speed output").glob(f"{site}_cam-*_speed.csv"):
        (folder / path.name).write_bytes(path.read_bytes())
    proj = project.open_folder(tmp_path, "174083")
    report = run.import_folder(proj, folder, use_qch=False)
    conn = db.open_db(proj.folder, proj.number)
    try:
        apply_fixture_decisions(conn, report.sites[0].location_id, want)
        got = loader.drawer(conn, report.sites[0].location_id)
    finally:
        conn.close()
    for block in ("directions", "histograms", "hourly", "bins", "posted_same", "status"):
        diffs = differences(got[block], want[block], block)
        assert diffs == [], f"{len(diffs)} differences in {block}, first: {diffs[:5]}"
    for g, w in zip(got["cams"], want["cams"]):
        assert differences({k: v for k, v in g.items()}, {k: v for k, v in w.items()}, "cam") == []
    for field in ("code", "slices", "is_sliced", "is_grouped", "count_days", "cams", "events", "masked_hours"):
        assert got["location"][field] == want["location"][field], field
    # what differs is only what the mockup invented: the name (no QC Hub here), and the paper trail
    assert [w for w in got["warnings"] if w["code"] != "not_in_qch"] == want["warnings"]


def test_the_real_weeklong_export_imports_and_lines_up_with_the_mockup_location_13(fixtures, samples, tmp_path):
    """17889013 (269,187 events, seven days) through the real import.

    The mockup's baked-in copy of this location is the same export hour for hour - the same number
    of rows in every direction and every date-hour - but not the same speeds everywhere: SB is
    identical, while WB on 2026-10-07 and 2026-10-08 and a few slow NB / EB rows carry other speeds
    in the mockup (its data was prepared separately). So this test pins what must hold: the file
    imports whole, the hours line up one for one, SB reproduces the mockup's numbers exactly, and
    the count window comes from the rows because the header's duration is 0.
    """
    from art_speed import contract, loader, project
    from art_speed.importer import run
    from art_speed.storage import db, store
    source = samples / "raw datalens speed output" / "17889013_cam-1_speed.csv"
    if not source.is_file():
        pytest.skip("the weeklong 17889013 export is not in sample reports")
    want = fixtures["locations"]["6"]
    folder = tmp_path / "csv"
    folder.mkdir()
    (folder / source.name).write_bytes(source.read_bytes())
    proj = project.open_folder(tmp_path, "178890")
    report = run.import_folder(proj, folder, use_qch=False)
    assert report.status == run.IMPORTED and report.sites[0].events == 269187
    assert any("duration of 0" in a for a in report.sites[0].advisories)
    conn = db.open_db(proj.folder, proj.number)
    try:
        loc = report.sites[0].location_id
        apply_fixture_decisions(conn, loc, want)
        with db.transaction(conn):                               # the mockup opens with two hours masked
            store.set_masked(conn, 17889013, 1, want["cams"][0]["masked_hours"], True)
        got = loader.drawer(conn, loc)
    finally:
        conn.close()
    assert contract.check_drawer(got) == []
    assert got["location"]["window"] == {"start_date": "2026-10-06", "start_time": "12:00 AM",
                                         "duration_hours": 168, "source": "rows"}
    assert got["location"]["count_days"] == want["location"]["count_days"]
    assert (got["cams"][0]["events"], got["cams"][0]["csv_form"]) == (269187, "raw")
    key = "17889013:1"
    for d in ("NB", "SB", "EB", "WB"):
        mine, mock = got["bins"][key][d], want["bins"][key][d]
        assert sorted(mine) == sorted(mock), f"{d}: the hours with data"
        assert all(sum(mine[h].values()) == sum(mock[h].values()) for h in mine), f"{d}: rows per hour"
    assert got["bins"][key]["SB"] == want["bins"][key]["SB"]
    assert differences(got["cams"][0]["directions"]["SB"], want["cams"][0]["directions"]["SB"], "SB") == []
    assert differences(got["histograms"][key]["SB"], want["histograms"][key]["SB"], "SB") == []
    assert differences(got["directions"]["SB"]["report"], want["directions"]["SB"]["report"], "SB") == []
    assert differences(got["hourly"]["directions"]["SB"], want["hourly"]["directions"]["SB"], "SB") == []
