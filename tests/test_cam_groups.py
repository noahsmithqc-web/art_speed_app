"""Cam groups: in a grouped location the same cam number on every sitenumber is one camera -
one choice in Decisions, one corner, statistics and report rows over all of its sitenumbers."""

from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from art_speed import contract, edits, loader, payload, project, report, server
from art_speed.storage import db, store
from tests import helpers
from tests.test_engine_fixtures import differences

A, B, C = 17814028, 17814029, 17814030            # three 24-hour sitenumbers, one day each
DAY = {A: "2026-10-23", B: "2026-10-24", C: "2026-10-25"}


def day_rows(site, speeds, direction="EB", hour=8):
    return helpers.event_rows(direction, speeds, date=DAY[site], hour=hour)


@pytest.fixture
def proj(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    return project.open_folder(folder, "178140")


@pytest.fixture
def conn(proj):
    c, _ = db.open_or_create(proj.folder, proj.number)
    yield c
    c.close()


@pytest.fixture
def grouped(conn):
    """A + B + C as one location. Cam 1 on all three (5 + 4 + 3 EB rows, one zero), cam 2 on all
    three but thin on C, cam 3 on A and B only."""
    helpers.add_site(conn, A, {1: day_rows(A, [0, 20, 22, 24, 26]), 2: day_rows(A, [30, 31, 32, 33]),
                               3: day_rows(A, [40])}, make_location=False)
    helpers.add_site(conn, B, {1: day_rows(B, [28, 30, 32, 34]), 2: day_rows(B, [34, 35, 36, 37]),
                               3: day_rows(B, [41])}, make_location=False)
    helpers.add_site(conn, C, {1: day_rows(C, [36, 38, 40]), 2: day_rows(C, [38])}, make_location=False)
    with db.transaction(conn):
        return store.create_location(conn, [{"sitenumber": n} for n in (A, B, C)])


def test_one_cam_per_cam_number_with_statistics_over_every_sitenumber(conn, grouped):
    d = loader.drawer(conn, grouped)
    assert contract.check_drawer(d) == []
    assert [(c["key"], c["label"]) for c in d["cams"]] == [
        (f"{A}:1", "Cam 1"), (f"{A}:2", "Cam 2"), (f"{A}:3", "Cam 3")]
    cam1 = d["cams"][0]
    assert cam1["directions"]["EB"]["n"] == 12 and cam1["directions"]["EB"]["zero"] == 1
    assert cam1["events"] == 12 and cam1["hours_with_data"] == 3
    assert cam1["on_sitenumbers"] == {"set": 3, "of": 3}
    assert [(m["label"], m["events"]) for m in cam1["members"]] == [
        ("Cam 1 · 28", 5), ("Cam 1 · 29", 4), ("Cam 1 · 30", 3)]
    assert d["histograms"][f"{A}:1"]["EB"]["events"] == 12
    assert d["location"]["cams"] == 3 and d["location"]["count_days"] == sorted(DAY.values())
    assert sorted(d["bins"]) == sorted(f"{n}:{c}" for n in (A, B, C) for c in (1, 2, 3) if (n, c) != (C, 3))
    # a dump of the drawer rebuilds to the same drawer
    assert differences(payload.build_drawer(payload.from_drawer(d)), d) == []


def test_a_stored_member_means_the_group_and_the_report_holds_every_day(conn, grouped):
    with db.transaction(conn):                           # as an older db has it: one sitenumber's cam
        conn.execute("UPDATE speed_direction SET posted_mph = 25, source_sitenumber = ?, source_cam = 1 "
                     "WHERE direction = 'EB'", (B,))
        conn.execute("UPDATE speed_direction SET include_in_report = 0 WHERE direction <> 'EB'")
        conn.execute("UPDATE speed_cam SET corner = 'SE'")
        store.set_manual_site(conn, A, street_ns="Seminole Dr", street_ew="Mulholland Hwy")
    d = loader.drawer(conn, grouped)
    eb = d["directions"]["EB"]
    assert eb["source_cam"] == f"{A}:1" and eb["report"]["events"] == 12 and eb["report"]["n"] == 11
    assert [day["date"] for day in d["hourly"]["directions"]["EB"]["days"]] == sorted(DAY.values())
    rows = report.gather(conn, grouped).rows["EB"]
    assert [r[4] for r in rows] == [20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 40]
    assert [r[0] for r in rows] == [DAY[A]] * 4 + [DAY[B]] * 4 + [DAY[C]] * 3


def test_warnings_for_a_cam_missing_from_a_sitenumber_and_a_thin_file(conn, grouped):
    warnings = {w["code"]: w["text"] for w in loader.drawer(conn, grouped)["warnings"]}
    assert warnings["partial_cam"] == f"Cam 3 is on 2 of 3 sitenumbers (not on {C})"
    texts = [w["text"] for w in loader.drawer(conn, grouped)["warnings"] if w["code"] == "weak_member"]
    assert texts == ["Cam 3 · 28 has 1 event against 5 on cam 1 · 28",
                     "Cam 3 · 29 has 1 event against 4 on cam 1 · 29",
                     "Cam 2 · 30 has 1 event against 3 on cam 1 · 30"]


def test_a_dead_file_inside_a_live_group_is_named(conn):
    helpers.add_site(conn, A, {1: day_rows(A, [20, 21])}, make_location=False)
    helpers.add_site(conn, B, {1: day_rows(B, [0, 0])}, make_location=False)
    with db.transaction(conn):
        loc = store.create_location(conn, [{"sitenumber": A}, {"sitenumber": B}])
    d = loader.drawer(conn, loc)
    assert not d["cams"][0]["dead"] and [m["dead"] for m in d["cams"][0]["members"]] == [False, True]
    assert {"code": "dead_cam", "text": "Cam 1 · 29 dead (100 % zero speeds)"} in d["warnings"]


def test_corner_time_mask_and_acknowledgement_reach_every_member(conn, grouped):
    with db.transaction(conn):
        edits.set_corner(conn, grouped, f"{B}:1", "SE")           # any member names the group
        edits.set_source_cam(conn, grouped, "EB", f"{C}:1")
        edits.set_time_mask(conn, grouped, f"{A}:1", [f"{DAY[B]} 08", f"{DAY[C]} 08"], True)
    corners = dict(conn.execute("SELECT sitenumber, corner FROM speed_cam WHERE cam = 1"))
    assert corners == {A: "SE", B: "SE", C: "SE"}
    assert conn.execute("SELECT source_sitenumber, source_cam FROM speed_direction "
                        "WHERE direction = 'EB'").fetchone()[:] == (A, 1)
    assert store.masked_hours(conn, A, 1) == [] and store.masked_hours(conn, B, 1) == [f"{DAY[B]} 08"]
    d = loader.drawer(conn, grouped)
    assert d["cams"][0]["corner"] == "SE" and d["cams"][0]["masked_hours"] == [f"{DAY[B]} 08", f"{DAY[C]} 08"]
    assert d["directions"]["EB"]["report"]["events"] == 5 and d["cams"][0]["over_half_masked"]
    assert [t["what"] for t in d["paper_trail"][:4]] == [
        f"Cam 1: masked {DAY[C]} 08:00", f"Cam 1: masked {DAY[B]} 08:00",
        "EB source cam set to cam 1", "Cam 1 corner set to SE (all sitenumbers)"]
    with db.transaction(conn):
        edits.acknowledge_mask(conn, grouped, f"{A}:1")
    assert loader.drawer(conn, grouped)["cams"][0]["mask_acknowledged"]
    with pytest.raises(edits.EditError, match="has no data at"):
        with db.transaction(conn):
            edits.set_time_mask(conn, grouped, f"{A}:1", [f"{DAY[A]} 23"], True)


def test_ungroup_and_group_carry_the_source_cam_by_cam_number(conn, grouped):
    with db.transaction(conn):
        edits.set_source_cam(conn, grouped, "EB", f"{A}:3")
        parts = edits.ungroup(conn, grouped)
    sources = [store.load_location(conn, i)["directions"]["EB"] for i in parts]
    assert [(s["source_sitenumber"], s["source_cam"]) for s in sources] == [(A, 3), (B, 3), (None, None)]
    with db.transaction(conn):
        again = edits.group(conn, parts[1:] + parts[:1])
    assert loader.drawer(conn, again)["directions"]["EB"]["source_cam"] == f"{A}:3"


def test_bin_export_of_a_cam_group_is_every_sitenumber(proj, conn, grouped):
    conn.commit()
    client = TestClient(server.create_app(proj))
    r = client.get(f"/speed/export/{A}/1/EB", params={"bin": "30-39", "location": grouped})
    assert r.headers["content-disposition"] == f'attachment; filename="{A}_{B}_{C}_cam-1_EB_30-39mph.csv"'
    rows = list(csv.reader(io.StringIO(r.text)))[1:]
    assert [(x[0], x[4], x[6]) for x in rows] == [
        (DAY[B], "30", str(B)), (DAY[B], "32", str(B)), (DAY[B], "34", str(B)),
        (DAY[C], "36", str(C)), (DAY[C], "38", str(C))]
