"""SP-1: what goes into speed_######.db and comes back out."""

from __future__ import annotations

import sqlite3

import pytest

from art_speed.qch import site as qch_site
from art_speed.storage import db, store
from tests import helpers


@pytest.fixture
def conn(tmp_path):
    c = helpers.open_db(tmp_path)
    yield c
    c.close()


def test_a_site_with_cams_and_raw_rows(conn):
    rows1 = helpers.event_rows("NB", [20, 0, 31]) + helpers.event_rows("SB", [25], start_line=10)
    helpers.add_site(conn, 17408304, {1: rows1, 2: helpers.event_rows("NB", [22, 23])}, site=helpers.benning())
    assert store.sitenumbers(conn) == [17408304]
    assert [(c["cam"], c["events"], c["zero_events"], c["corner"]) for c in store.cams(conn, 17408304)] == [
        (1, 4, 1, None), (2, 2, 0, None)]
    assert store.cam_bins(conn, 17408304, 1) == {
        "NB": {"2026-02-24 06": {20: 1, 0: 1, 31: 1}}, "SB": {"2026-02-24 06": {25: 1}}}
    raw = store.cam_rows(conn, 17408304, 1, "NB")
    assert [(r["time"], r["speed_mph"], r["track_id"], r["movement"], r["class"]) for r in raw] == [
        ("06:00:00", 20, "1", "Thru", "cl2_passenger_car"), ("06:00:01", 0, "2", "Thru", "cl2_passenger_car"),
        ("06:00:02", 31, "3", "Thru", "cl2_passenger_car")]


def test_a_fresh_location_starts_from_the_lane_configs_and_the_qch_window(conn):
    loc_id = helpers.add_site(conn, 17408304, {1: helpers.event_rows("NB", [20])}, site=helpers.benning())
    loc = store.load_location(conn, loc_id)
    assert loc["slices"] == [{"sitenumber": 17408304, "date": None, "from_hour": None, "to_hour": None}]
    assert {d: r["include_in_report"] for d, r in loc["directions"].items()} == {
        "NB": 1, "SB": 1, "EB": 0, "WB": 1}                          # EB lane config is all zeros
    for r in loc["directions"].values():
        assert (r["posted_mph"], r["source_cam"], r["adjust_by"], r["mask_low"], r["mask_high"]) == (
            None, None, 0, 5, None)
    assert (loc["window_start_date"], loc["window_start_time"], loc["window_duration_hr"],
            loc["window_source"]) == ("2026-02-24", "06:00:00", 13.0, "QC Hub")
    assert (loc["posted_same_ns"], loc["posted_same_ew"], loc["name_source"], loc["note"]) == (1, 1, "qch", "")
    assert store.site_name(store.qch_row(conn, 17408304)) == "Benning Rd SE  -  A St SE"


def test_a_site_qch_does_not_have_assumes_four_approaches_and_uses_the_file_header(conn):
    loc_id = helpers.add_site(conn, 17999901, {1: helpers.event_rows("NB", [20])},
                              site=qch_site.Site(17999901, qch_site.NOT_FOUND))
    used, base = store.lane_configs(conn, 17999901)
    assert used == base == {d: "2000000" for d in db.DIRECTIONS}
    loc = store.load_location(conn, loc_id)
    assert all(r["include_in_report"] == 1 for r in loc["directions"].values())
    assert (loc["window_source"], loc["window_start_date"], loc["window_duration_hr"]) == (
        "file header", "2026-02-24", 13.0)


def test_manual_site_entry(conn):
    helpers.add_site(conn, 17999901, {1: helpers.event_rows("NB", [20])},
                     site=qch_site.Site(17999901, qch_site.NOT_FOUND))
    with db.transaction(conn):
        store.set_manual_site(conn, 17999901, street_ns=" Main St ", street_ew="1st Ave",
                              latitude=38.9, longitude=-77.0)
    row = store.qch_row(conn, 17999901)
    assert row["lookup"] == "manual" and store.site_name(row) == "Main St  -  1st Ave"
    assert (row["latitude"], row["longitude"]) == (38.9, -77.0)
    assert store.lane_configs(conn, 17999901)[0] == {d: "2000000" for d in db.DIRECTIONS}
    with pytest.raises(KeyError):
        store.set_manual_site(conn, 17000000, location_name="nowhere")


def test_lane_config_edits_live_beside_the_snapshot(conn):
    helpers.add_site(conn, 17408304, {1: helpers.event_rows("NB", [20])}, site=helpers.benning())
    with db.transaction(conn):
        store.set_lane_config(conn, 17408304, "EB", "2000000")
    used, base = store.lane_configs(conn, 17408304)
    assert (used["EB"], base["EB"]) == ("2000000", "0000000")
    assert store.qch_row(conn, 17408304)["eb_lane_config"] == "0000000"      # the snapshot is never edited
    with db.transaction(conn):
        store.set_lane_config(conn, 17408304, "EB", "0000000")               # back to QC Hub's value
    assert conn.execute("SELECT COUNT(*) FROM speed_lane_config").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction(conn):
            store.set_lane_config(conn, 17408304, "EB", "9")


def test_time_masks_belong_to_the_cam_and_a_change_clears_the_acknowledgement(conn):
    helpers.add_site(conn, 17408304, {1: helpers.event_rows("NB", [20])}, site=helpers.benning())
    with db.transaction(conn):
        store.set_masked(conn, 17408304, 1, ["2026-02-24 10", "2026-02-24 09", "2026-02-24 10"], True)
        conn.execute("UPDATE speed_cam SET mask_ack_utc = ? WHERE cam = 1", (db.now_utc(),))
    assert store.masked_hours(conn, 17408304, 1) == ["2026-02-24 09", "2026-02-24 10"]
    with db.transaction(conn):
        store.set_masked(conn, 17408304, 1, ["2026-02-24 09"], False)
    assert store.masked_hours(conn, 17408304, 1) == ["2026-02-24 10"]
    assert store.cams(conn, 17408304)[0]["mask_ack_utc"] is None


def test_split_locations_copy_decisions_and_take_the_slice_window(conn):
    parent = helpers.add_site(conn, 17408304, {1: helpers.event_rows("NB", [20])}, site=helpers.benning())
    with db.transaction(conn):
        conn.execute("UPDATE speed_direction SET posted_mph = 25, source_sitenumber = 17408304, "
                     "source_cam = 1, adjust_by = -2, mask_low = NULL WHERE location_id = ? "
                     "AND direction = 'NB'", (parent,))
        conn.execute("UPDATE speed_location SET note = 'queueing' WHERE location_id = ?", (parent,))
        day = store.create_location(conn, [{"sitenumber": 17408304, "date": "2026-02-24"}], seed_from=parent)
        am = store.create_location(conn, [{"sitenumber": 17408304, "date": "2026-02-24",
                                           "from_hour": 6, "to_hour": 10}], seed_from=parent)
    nb = store.load_location(conn, day)["directions"]["NB"]
    assert (nb["posted_mph"], nb["source_cam"], nb["adjust_by"], nb["mask_low"]) == (25, 1, -2, None)
    assert store.load_location(conn, day)["note"] == "queueing"
    w = store.load_location(conn, am)
    assert (w["window_start_date"], w["window_start_time"], w["window_duration_hr"], w["window_source"]) == (
        "2026-02-24", "06:00:00", 4, "slice")
    assert store.location_ids(conn) == [parent, day, am]      # unsliced, then by date, then by hour


def test_delete_sitenumber_removes_everything_stored_for_it(conn):
    helpers.add_site(conn, 17408304, {1: helpers.event_rows("NB", [20, 21])}, site=helpers.benning())
    keep = helpers.add_site(conn, 17408305, {1: helpers.event_rows("NB", [30])})
    with db.transaction(conn):
        store.set_masked(conn, 17408304, 1, ["2026-02-24 06"], True)
        store.set_lane_config(conn, 17408304, "EB", "2000000")
        gone = store.delete_sitenumber(conn, 17408304)
    assert gone["events"] == 2 and gone["cams"] == 1 and len(gone["locations_deleted"]) == 1
    for table in ("speed_event", "speed_cam", "speed_cam_mask", "speed_qch_site", "speed_lane_config",
                  "speed_slice", "speed_sitenumber", "speed_location", "speed_direction"):
        left = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert left == {"speed_direction": 4}.get(table, 1 if table != "speed_cam_mask" and table != "speed_lane_config" else 0), table
    assert store.location_ids(conn) == [keep]


def test_delete_from_a_grouped_location_keeps_the_rest_and_resets_its_decisions(conn):
    a = helpers.add_site(conn, 17408301, {1: helpers.event_rows("NB", [20])}, make_location=False)
    helpers.add_site(conn, 17408302, {1: helpers.event_rows("NB", [30])}, make_location=False)
    with db.transaction(conn):
        group = store.create_location(conn, [{"sitenumber": 17408301}, {"sitenumber": 17408302}])
        conn.execute("UPDATE speed_direction SET posted_mph = 25, source_sitenumber = 17408302, "
                     "source_cam = 1 WHERE location_id = ?", (group,))
        result = store.delete_sitenumber(conn, 17408301)
    assert a is None and result["locations_reset"] == [group] and result["locations_deleted"] == []
    loc = store.load_location(conn, group)
    assert [s["sitenumber"] for s in loc["slices"]] == [17408302]
    assert all(r["posted_mph"] is None and r["source_cam"] is None for r in loc["directions"].values())


def test_paper_trail_gathers_location_site_and_cam_lines(conn):
    loc = helpers.add_site(conn, 17408304, {1: helpers.event_rows("NB", [20])}, site=helpers.benning())
    other = helpers.add_site(conn, 17408305, {1: helpers.event_rows("NB", [20])})
    with db.transaction(conn):
        store.log(conn, "Posted speed NB set to 25", location_id=loc, who="nsmith", when="2026-11-11T10:00:00Z")
        store.log(conn, "Imported 1 file", sitenumber=17408304, who="nsmith", when="2026-11-11T09:00:00Z")
        store.log(conn, "Cam 1: masked 2026-02-24 10:00", sitenumber=17408304, cam=1, who="nsmith",
                  when="2026-11-11T11:00:00Z")
        store.log(conn, "Posted speed NB set to 30", location_id=other, who="nsmith")
        store.log(conn, "Report generated", who="nsmith")
    lines = store.trail(conn, loc)
    assert [(r["scope"], r["what"]) for r in lines] == [
        ("cam", "Cam 1: masked 2026-02-24 10:00"), ("location", "Posted speed NB set to 25"),
        ("site", "Imported 1 file")]
