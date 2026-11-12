"""SP-3: database -> engine -> contract. Decisions in the db show up as numbers in the payload."""

from __future__ import annotations

import pytest

from art_speed import contract, loader, payload, project
from art_speed.storage import db, store
from tests import helpers


@pytest.fixture
def conn(tmp_path):
    c = helpers.open_db(tmp_path)
    yield c
    c.close()


def two_cam_site(conn):
    """17408304: cam 1 (NE) and cam 2 (SW); NB and SB rows over two hours of one day."""
    cam1 = (helpers.event_rows("NB", [0, 4, 20, 22, 24, 26, 28, 30, 32, 60]) +
            helpers.event_rows("NB", [21, 23], hour=7, start_line=30) +
            helpers.event_rows("SB", [0, 0, 0, 18], start_line=50))
    cam2 = helpers.event_rows("SB", [25, 27, 29, 31]) + helpers.event_rows("NB", [0, 0, 19], start_line=40)
    loc = helpers.add_site(conn, 17408304, {1: cam1, 2: cam2}, site=helpers.benning())
    with db.transaction(conn):
        conn.execute("UPDATE speed_cam SET corner = 'NE' WHERE cam = 1")
        conn.execute("UPDATE speed_cam SET corner = 'SW' WHERE cam = 2")
    return loc


def test_a_fresh_location_needs_input_and_matches_the_contract(conn):
    loc = helpers.add_site(conn, 17408304, {1: helpers.event_rows("NB", [20, 30])}, site=helpers.benning())
    drawer = loader.drawer(conn, loc)
    assert contract.check_drawer(drawer) == []
    assert drawer["status"] == {"state": "needs_input", "label": "Needs input", "missing": [
        "NB has no source cam", "NB has no posted speed", "SB has no source cam", "SB has no posted speed",
        "WB has no source cam", "WB has no posted speed"]}
    loc_block = drawer["location"]
    assert (loc_block["code"], loc_block["name"], loc_block["geometry_source"]) == (
        "04", "Benning Rd SE  -  A St SE", "qch")
    assert loc_block["window"] == {"start_date": "2026-02-24", "start_time": "06:00 AM",
                                   "duration_hours": 13, "source": "QC Hub"}
    assert (loc_block["latitude"], loc_block["longitude"]) == (38.8884457, -76.9369868)
    assert loc_block["sitenumbers"][0]["street_ns"] == "Benning Rd SE"
    assert drawer["directions"]["EB"]["exists"] is False and drawer["directions"]["EB"]["include_in_report"] is False
    assert drawer["directions"]["NB"]["suggested"] == {"cam": "17408304:1", "why": "no on-axis cam · lowest % zero"}


def test_decisions_drive_the_report_values(conn):
    loc = two_cam_site(conn)
    with db.transaction(conn):
        conn.execute("UPDATE speed_direction SET posted_mph = 25, source_sitenumber = 17408304, source_cam = 1 "
                     "WHERE direction = 'NB'")
        conn.execute("UPDATE speed_direction SET posted_mph = 25, source_sitenumber = 17408304, source_cam = 2, "
                     "adjust_by = -2 WHERE direction = 'SB'")
        conn.execute("UPDATE speed_direction SET include_in_report = 0 WHERE direction = 'WB'")
    drawer = loader.drawer(conn, loc)
    assert contract.check_drawer(drawer) == []
    assert drawer["status"]["state"] == "ready"
    nb = drawer["directions"]["NB"]["report"]
    # cam 1 NB: 12 rows, 1 zero, the 4 is below the default floor -> 10 kept
    assert (nb["events"], nb["zero"], nb["masked"], nb["n"], nb["over_posted"]) == (12, 1, 1, 10, 5)
    assert nb["avg"] == pytest.approx((20 + 22 + 24 + 26 + 28 + 30 + 32 + 60 + 21 + 23) / 10)
    sb = drawer["directions"]["SB"]["report"]
    assert (sb["n"], sb["adjust_by"], sb["avg"], sb["over_posted"]) == (4, -2, 26.0, 2)   # 23, 25, 27, 29
    assert drawer["directions"]["NB"]["suggested"] == {"cam": "17408304:1", "why": "primary corner"}
    assert drawer["directions"]["SB"]["suggested"] == {"cam": "17408304:2", "why": "primary corner"}
    cams = {c["key"]: c for c in drawer["cams"]}
    assert cams["17408304:1"]["directions"]["SB"]["pct_zero"] == 0.75
    assert cams["17408304:1"]["directions"]["NB"]["role"] == "Primary"
    assert drawer["histograms"]["17408304:1"]["NB"]["bins10"][-1] == {
        "from": 60, "to": 69, "n": 1, "above_upper_mask": False}
    row = payload.tab_row(drawer)
    assert (row["corners"], row["posted"], row["source_cams"], row["in_report"]) == (
        {"set": 2, "of": 2}, {"set": 2, "of": 2}, {"set": 2, "of": 2}, ["NB", "SB"])


def test_a_time_mask_removes_the_hour_from_every_statistic_and_warns_over_half(conn):
    loc = two_cam_site(conn)
    with db.transaction(conn):
        conn.execute("UPDATE speed_direction SET posted_mph = 25, source_sitenumber = 17408304, source_cam = 1 "
                     "WHERE direction IN ('NB', 'SB')")
        store.set_masked(conn, 17408304, 1, ["2026-02-24 07"], True)
    drawer = loader.drawer(conn, loc)
    nb = drawer["directions"]["NB"]["report"]
    assert (nb["n"], nb["masked_hours"]) == (8, 1)
    cell = drawer["hourly"]["directions"]["NB"]["days"][0]["cells"][7]
    assert cell["masked"] and cell["n"] == 2 and not cell["peak"]            # still shown, struck through
    assert drawer["hourly"]["directions"]["NB"]["shared_with"] == ["SB"]
    assert drawer["location"]["masked_hours"] == 1 and drawer["warnings"] == []
    with db.transaction(conn):
        store.set_masked(conn, 17408304, 1, ["2026-02-24 06"], True)        # now both of its hours
    drawer = loader.drawer(conn, loc)
    assert drawer["warnings"] == [{"code": "over_half_masked", "text": "Cam 1: over half the hours masked",
                                   "cam": "17408304:1", "can_acknowledge": True}]
    assert drawer["directions"]["NB"]["report"]["n"] == 0 and drawer["directions"]["NB"]["report"]["p85"] is None
    with db.transaction(conn):
        conn.execute("UPDATE speed_cam SET mask_ack_utc = ? WHERE cam = 1", (db.now_utc(),))
    assert loader.drawer(conn, loc)["warnings"] == []


def test_split_by_day_and_by_time_use_the_slice_filter(conn):
    rows = (helpers.event_rows("NB", [20, 21, 22], date="2026-10-06", hour=6) +
            helpers.event_rows("NB", [40, 41], date="2026-10-06", hour=16, start_line=20) +
            helpers.event_rows("NB", [30, 31, 32, 33], date="2026-10-07", hour=6, start_line=30))
    parent = helpers.add_site(conn, 17889013, {1: rows}, header={"header_start_date": "2026-10-06",
                                                                 "header_start_time": "00:00:00",
                                                                 "header_duration_hr": 48.0})
    with db.transaction(conn):
        conn.execute("UPDATE speed_direction SET posted_mph = 35, source_sitenumber = 17889013, source_cam = 1")
        day2 = store.create_location(conn, [{"sitenumber": 17889013, "date": "2026-10-07"}], seed_from=parent)
        am = store.create_location(conn, [{"sitenumber": 17889013, "date": "2026-10-06", "from_hour": 6,
                                           "to_hour": 10}], seed_from=parent)
    whole, d2, a = (loader.drawer(conn, i) for i in (parent, day2, am))
    assert whole["directions"]["NB"]["report"]["n"] == 9 and whole["location"]["count_days"] == ["2026-10-06", "2026-10-07"]
    assert d2["directions"]["NB"]["report"]["n"] == 4 and d2["location"]["code"] == "13 10-07"
    assert d2["location"]["slices"][0]["label"] == "17889013 · Wed 10-07" and d2["location"]["is_sliced"]
    assert a["directions"]["NB"]["report"]["n"] == 3 and a["location"]["code"] == "13 10-06 06–10"
    assert a["location"]["window"] == {"start_date": "2026-10-06", "start_time": "06:00 AM",
                                       "duration_hours": 4, "source": "slice"}
    assert a["hourly"]["dates"] == ["2026-10-06"] and a["cams"][0]["events_in_location"] == 3
    assert a["cams"][0]["events"] == 9                                      # the file's total never changes
    assert [r["code"] for r in loader.tab(conn)["rows"]] == ["13", "13 10-06 06–10", "13 10-07"]


def test_grouped_location_has_one_cam_per_cam_number(conn):
    helpers.add_site(conn, 17408301, {1: helpers.event_rows("NB", [20, 21])}, make_location=False)
    helpers.add_site(conn, 17408302, {1: helpers.event_rows("NB", [30]), 2: helpers.event_rows("SB", [31])},
                     make_location=False)
    with db.transaction(conn):
        group = store.create_location(conn, [{"sitenumber": 17408301}, {"sitenumber": 17408302}])
    drawer = loader.drawer(conn, group)
    assert drawer["location"]["code"] == "01+02" and drawer["location"]["is_grouped"]
    assert [(c["key"], c["label"]) for c in drawer["cams"]] == [("17408301:1", "Cam 1"), ("17408302:2", "Cam 2")]
    assert [m["label"] for m in drawer["cams"][0]["members"]] == ["Cam 1 · 01", "Cam 1 · 02"]
    assert drawer["cams"][0]["directions"]["NB"]["n"] == 3                  # both sitenumbers' rows
    assert drawer["status"]["missing"][0] == "Corners needed for every live cam (Cam 1, Cam 2)"
    assert contract.check_drawer(drawer) == []


def test_lasting_import_warnings_header_vs_qch_and_not_in_qch(conn):
    a = helpers.add_site(conn, 17408304, {1: helpers.event_rows("NB", [20])}, site=helpers.benning(),
                         header={"header_start_date": "2026-02-25", "header_duration_hr": 12.0})
    b = helpers.add_site(conn, 17999901, {1: helpers.event_rows("NB", [20])})
    assert {"code": "header_vs_qch", "text": "File header disagrees with QC Hub (start date 2026-02-25 vs "
            "2026-02-24; duration 12 h vs 13 h)"} in loader.drawer(conn, a)["warnings"]
    codes = [w["code"] for w in loader.drawer(conn, b)["warnings"]]
    assert "not_in_qch" in codes and loader.drawer(conn, b)["location"]["geometry_source"] == "manual"


def test_tab_and_whole_dump(conn, tmp_path):
    two_cam_site(conn)
    tab = loader.tab(conn)
    assert contract.check_tab(tab) == []
    assert tab["summary"]["sitenumbers"] == 1 and tab["summary"]["cams"] == 2 and tab["summary"]["last_report"] is None
    assert tab["generate"] == {"enabled": False, "green": False}
    proj = project.Project(tmp_path, "174083", "x")
    assert contract.check_fixtures(loader.fixtures(conn, proj)) == []


def test_paper_trail_is_local_time_newest_first(conn):
    loc = helpers.add_site(conn, 17408304, {1: helpers.event_rows("NB", [20])}, site=helpers.benning())
    with db.transaction(conn):
        store.log(conn, "Posted speed NB set to 25", location_id=loc, who="nsmith", when="2026-11-11T10:00:00Z")
        store.log(conn, "Cam 1: masked 2026-02-24 10:00", sitenumber=17408304, cam=1, who="nsmith",
                  when="2026-11-11T11:00:00Z")
    trail = loader.drawer(conn, loc)["paper_trail"]
    assert [(t["scope"], t["what"], t.get("sitenumber")) for t in trail] == [
        ("cam", "Cam 1: masked 2026-02-24 10:00", "17408304"), ("location", "Posted speed NB set to 25", None)]
    assert trail[0]["when"] == loader.local_minute("2026-11-11T11:00:00Z") and len(trail[0]["when"]) == 16


def test_labels():
    assert payload.clock12("00:00:00") == "12:00 AM" and payload.clock12("13:30:00") == "01:30 PM"
    assert payload.clock24("06:00 AM") == "06:00:00" and payload.clock24("6:15 pm") == "18:15:00"
    assert payload.clock24("18:00") == "18:00:00" and payload.clock24("25:00") is None and payload.clock24("x") is None
    assert payload.describe_hours(["2026-10-07 11", "2026-10-07 10"]) == "2026-10-07 10:00–12:00"
    assert payload.describe_hours(["2026-10-07 10"]) == "2026-10-07 10:00"
    assert payload.describe_hours(["2026-10-07 10", "2026-10-07 14", "2026-10-07 15", "2026-10-08 00"]) == (
        "2026-10-07 10:00–11:00, 14:00–16:00; 2026-10-08 00:00–01:00")
