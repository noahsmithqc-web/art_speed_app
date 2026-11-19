"""SP-2: folder import - grouping by sitenumber, checks, delete-and-replace, the nm hint."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from art_speed import project
from art_speed.importer import nm_hint, run, scan
from art_speed.storage import db, store
from tests import helpers
from tests.fakes import qch as fakes
from tests.test_project import art_folder


@pytest.fixture
def csvs(tmp_path):
    folder = tmp_path / "csv"
    folder.mkdir()
    helpers.write_csv(folder, 17408304, 1, helpers.speed_rows("NB", [20, 0, 31]) + helpers.speed_rows("SB", [25]))
    helpers.write_csv(folder, 17408304, 2, helpers.speed_rows("NB", [22, 23]))
    helpers.write_csv(folder, 17408305, 1, helpers.speed_rows("NB", [0, 0]))
    (folder / "readme.txt").write_text("not a speed export")
    return folder


@pytest.fixture
def proj(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    return project.open_folder(folder, "174083")


def open_conn(proj):
    return db.open_db(proj.folder, proj.number)


def test_scan_groups_by_sitenumber_and_lists_what_it_skipped(csvs):
    found = scan.scan_folder(csvs)
    assert sorted(found.sites) == [17408304, 17408305]
    assert found.sites[17408304].cams == [1, 2] and found.sites[17408304].events == 6
    assert found.unmatched == ["readme.txt"] and not found.fatal and not found.needs_choice


def test_import_stores_sites_cams_rows_and_one_location_each(csvs, proj):
    qch = fakes.FakeQch(fakes.tm_site(17408304))
    report = run.import_folder(proj, csvs, qch=qch)
    assert report.status == run.IMPORTED and report.created_db
    assert [(s.sitenumber, s.cams, s.events, s.overwritten, s.qch_lookup) for s in report.sites] == [
        (17408304, [1, 2], 6, False, "legacycounts"), (17408305, [1], 2, False, "not_found")]
    assert report.sites[0].name == "Benning Rd SE  -  A St SE"
    assert report.unmatched == ["readme.txt"]
    conn = open_conn(proj)
    try:
        assert store.sitenumbers(conn) == [17408304, 17408305]
        assert conn.execute("SELECT COUNT(*) FROM speed_event").fetchone()[0] == 8
        assert store.location_ids(conn) == [s.location_id for s in report.sites]
        loc = store.load_location(conn, report.sites[0].location_id)
        assert loc["window_source"] == "QC Hub" and loc["nm_grouping_used"] == 0
        assert store.last_import(conn)["events"] == 8
        whats = [r["what"] for r in store.trail(conn, report.sites[0].location_id)]
        assert any(w.startswith("Imported 2 files (cams 1, 2), 6 events") for w in whats)
        assert any(w.startswith("QC Hub: in QC Hub as a turn movement site") for w in whats)
    finally:
        conn.close()


def test_import_checks(csvs, proj):
    # QC Hub says the count was on another day, 12 h, and gives EB no lanes; cam 1 of 05 is all zeros
    helpers.write_csv(csvs, 17408304, 3, helpers.speed_rows("EB", [18]) + helpers.speed_rows("NB", [30], hour=20)
                      + helpers.speed_rows("NB", [30, 30], hour=7)[:1] * 2)
    qch = fakes.FakeQch(fakes.tm_site(17408304, dates=("2026-02-25",), end="18:00:00"))
    report = run.import_folder(proj, csvs, qch=qch)
    site04, site05 = report.sites
    assert any("Header start date 2026-02-24 is not QC Hub's count date 2026-02-25" in w for w in site04.warnings)
    assert any("Header duration 13 h is not QC Hub's 12 h" in w for w in site04.warnings)
    assert any("1 EB events in an approach with no lanes" in w for w in site04.warnings)
    assert any("identical line" in a for a in site04.advisories)
    assert any("timestamped outside the count window" in a for a in site04.advisories)
    assert "Cam 1 dead (100 % zero speeds)" in site05.warnings
    assert any("Not in QC Hub" in w for w in site05.warnings)


def test_qch_down_never_stalls_the_import(csvs, proj):
    report = run.import_folder(proj, csvs, qch=fakes.FakeQch(fail_on="legacycounts"))
    assert report.status == run.IMPORTED and "QC Hub was not reached" in report.message
    assert all(s.qch_lookup is None for s in report.sites)
    assert all("Not checked against QC Hub: four approaches assumed" in s.warnings for s in report.sites)
    conn = open_conn(proj)
    try:
        assert store.lane_configs(conn, 17408304)[0] == {d: "2000000" for d in db.DIRECTIONS}
    finally:
        conn.close()


def test_a_bad_file_is_refused_and_named_and_the_rest_imports(csvs, proj):
    bad = csvs / "17408304_cam-2_speed.csv"
    bad.write_text(bad.read_text().replace("NB Thru", "NB Left"))
    report = run.import_folder(proj, csvs, use_qch=False)
    assert report.status == run.IMPORTED
    assert [f["file"] for f in report.refused_files] == ["17408304_cam-2_speed.csv"]
    assert "Thru movements only" in report.refused_files[0]["error"]
    assert report.sites[0].cams == [1]


def test_sitenumber_disagreement_stops_and_asks_then_follows_the_choice(csvs, proj):
    helpers.write_csv(csvs, 17408306, 1, helpers.speed_rows("NB", [20]), header_sitecode="17408307")
    asked = run.import_folder(proj, csvs, use_qch=False)
    assert asked.status == run.NEEDS_CHOICE and not db.exists(proj.folder, proj.number)
    assert asked.disagreements == [{"file": "17408306_cam-1_speed.csv", "file_name_sitenumber": 17408306,
                                    "header_sitecode": "17408307", "header_usable": True}]
    done = run.import_folder(proj, csvs, use_qch=False,
                             choices={"17408306_cam-1_speed.csv": scan.USE_HEADER})
    assert done.status == run.IMPORTED and [s.sitenumber for s in done.sites] == [17408304, 17408305, 17408307]
    conn = open_conn(proj)
    try:
        assert store.sitenumber_row(conn, 17408307)["sitenumber_from"] == "header"
        assert any("sitenumber taken from the header" in r["what"]
                   for r in store.trail(conn, done.sites[2].location_id))
    finally:
        conn.close()


def test_the_same_cam_twice_in_one_batch_refuses_everything(csvs, proj):
    helpers.write_csv(csvs, 17408399, 1, helpers.speed_rows("NB", [20]), header_sitecode="17408304")
    report = run.import_folder(proj, csvs, use_qch=False,
                               choices={"17408399_cam-1_speed.csv": scan.USE_HEADER})
    assert report.status == run.REFUSED and "17408304 cam 1 appears 2 times" in report.fatal[0]
    assert not db.exists(proj.folder, proj.number)


def test_reimport_is_delete_and_replace_and_resets_every_decision(csvs, proj):
    first = run.import_folder(proj, csvs, use_qch=False)
    loc_id = first.sites[0].location_id
    conn = open_conn(proj)
    with db.transaction(conn):
        conn.execute("UPDATE speed_direction SET posted_mph = 25, source_sitenumber = 17408304, source_cam = 2, "
                     "adjust_by = -2, mask_high = 60 WHERE location_id = ?", (loc_id,))
        conn.execute("UPDATE speed_cam SET corner = 'NE' WHERE sitenumber = 17408304")
        conn.execute("UPDATE speed_location SET note = 'x', name_source = 'manual', manual_name = 'M' "
                     "WHERE location_id = ?", (loc_id,))
        store.set_masked(conn, 17408304, 1, ["2026-02-24 06"], True)
        store.set_lane_config(conn, 17408304, "EB", "0000000")
        split = store.create_location(conn, [{"sitenumber": 17408304, "date": "2026-02-24"}], seed_from=loc_id)
    conn.close()
    (csvs / "17408305_cam-1_speed.csv").unlink()              # only 17408304 comes in again
    helpers.write_csv(csvs, 17408304, 2, helpers.speed_rows("NB", [40, 41, 42]))
    again = run.import_folder(proj, csvs, use_qch=False)
    assert [(s.sitenumber, s.overwritten, s.events) for s in again.sites] == [(17408304, True, 7)]
    conn = open_conn(proj)
    try:
        new_id = again.sites[0].location_id
        assert new_id not in (loc_id, split)
        assert store.location_ids(conn) == [new_id, first.sites[1].location_id]    # the split is gone too
        loc = store.load_location(conn, new_id)
        assert (loc["note"], loc["name_source"], loc["manual_name"]) == ("", "qch", None)
        for r in loc["directions"].values():
            assert (r["posted_mph"], r["source_cam"], r["adjust_by"], r["mask_low"], r["mask_high"]) == (
                None, None, 0, 5, None)
        assert [c["corner"] for c in store.cams(conn, 17408304)] == [None, None]
        assert store.masked_hours(conn, 17408304, 1) == []
        assert store.lane_configs(conn, 17408304)[0]["EB"] == "2000000"
        assert conn.execute("SELECT COUNT(*) FROM speed_event WHERE sitenumber = 17408304").fetchone()[0] == 7
        assert conn.execute("SELECT COUNT(*) FROM speed_event WHERE sitenumber = 17408305").fetchone()[0] == 2
        assert any("Re-imported" in r["what"] for r in store.trail(conn, new_id))
    finally:
        conn.close()


def test_reimport_into_a_grouped_location_keeps_the_group_and_resets_it(csvs, proj):
    run.import_folder(proj, csvs, use_qch=False)
    conn = open_conn(proj)
    with db.transaction(conn):
        for loc in store.location_ids(conn):
            store.delete_location(conn, loc)
        group = store.create_location(conn, [{"sitenumber": 17408304}, {"sitenumber": 17408305}])
        conn.execute("UPDATE speed_direction SET posted_mph = 25 WHERE location_id = ?", (group,))
    conn.close()
    (csvs / "17408305_cam-1_speed.csv").unlink()
    again = run.import_folder(proj, csvs, use_qch=False)
    conn = open_conn(proj)
    try:
        assert again.sites[0].location_id == group and store.location_ids(conn) == [group]
        loc = store.load_location(conn, group)
        assert [s["sitenumber"] for s in loc["slices"]] == [17408304, 17408305]      # same order as before
        assert all(r["posted_mph"] is None for r in loc["directions"].values())
    finally:
        conn.close()


def test_dry_run_writes_nothing(csvs, proj):
    report = run.import_folder(proj, csvs, use_qch=False, dry_run=True)
    assert report.status == run.SCANNED and len(report.sites) == 2
    assert list(proj.folder.iterdir()) == []


def test_empty_or_missing_folder(tmp_path, proj):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run.import_folder(proj, empty).status == run.REFUSED
    assert "is not a folder" in run.import_folder(proj, tmp_path / "nope").message
    assert list(proj.folder.iterdir()) == []


def test_delete_sitenumber_from_the_table_menu(csvs, proj):
    run.import_folder(proj, csvs, use_qch=False)
    gone = run.delete_sitenumber(proj, 17408305)
    assert gone["events"] == 2
    conn = open_conn(proj)
    try:
        assert store.sitenumbers(conn) == [17408304]
        assert conn.execute("SELECT what FROM speed_log WHERE scope = 'project'").fetchone()[0].startswith(
            "Sitenumber 17408305 deleted")
    finally:
        conn.close()
    with pytest.raises(KeyError):
        run.delete_sitenumber(proj, 17408305)


# ------------------------------------------------------------------ the near miss grouping hint

def make_nm_db(folder, number="174083"):
    """A stand-in nm_######.db with just the two tables and columns the hint reads."""
    path = folder / f"nm_{number}.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE nm_location (nm_location_id INTEGER PRIMARY KEY, nm_location_code TEXT);
        CREATE TABLE nm_sitecode (nm_sitecode TEXT PRIMARY KEY, nm_sitenumber INTEGER,
                                  nm_location_id INTEGER, qch_sitenumber INTEGER);
        INSERT INTO nm_location VALUES (1, '04,05'), (2, '06');
        INSERT INTO nm_sitecode VALUES ('04', 17408304, 1, NULL), ('05', 17408399, 1, 17408305),
                                       ('06', 17408306, 2, NULL);
    """)
    conn.commit()
    conn.close()
    return path


def test_sitenumbers_sharing_a_near_miss_location_start_grouped_and_the_nm_db_is_untouched(csvs, tmp_path):
    folder = art_folder(tmp_path)
    nm = make_nm_db(folder)
    before = hashlib.sha256(nm.read_bytes()).hexdigest(), nm.stat().st_mtime_ns
    proj = project.open_folder(folder)
    assert nm_hint.read(folder, "174083") == {17408304: "04,05", 17408305: "04,05", 17408306: "06"}
    report = run.import_folder(proj, csvs, use_qch=False)
    assert report.sites[0].location_id == report.sites[1].location_id
    conn = open_conn(proj)
    try:
        loc = store.load_location(conn, report.sites[0].location_id)
        assert [s["sitenumber"] for s in loc["slices"]] == [17408304, 17408305] and loc["nm_grouping_used"] == 1
        assert store.sitenumber_row(conn, 17408305)["nm_location_code"] == "04,05"
        assert any("share near miss location 04,05" in r["what"] for r in store.trail(conn, loc["location_id"]))
    finally:
        conn.close()
    assert (hashlib.sha256(nm.read_bytes()).hexdigest(), nm.stat().st_mtime_ns) == before
    assert sorted(p.name for p in folder.iterdir()) == ["174083.json", "nm_174083.db", "speed_174083.db"]


def test_no_hint_outside_an_art_project_or_from_an_unreadable_nm_db(csvs, proj, tmp_path):
    make_nm_db(proj.folder)                              # there, but the folder is not an A.R.T. project
    report = run.import_folder(proj, csvs, use_qch=False)
    assert report.sites[0].location_id != report.sites[1].location_id
    broken = tmp_path / "b"
    broken.mkdir()
    (broken / "nm_174083.db").write_text("not a database")
    assert nm_hint.read(broken, "174083") == {}
    assert nm_hint.read(tmp_path / "nowhere", "174083") == {}


def test_the_real_folder_imports(samples, proj):
    report = run.import_folder(proj, samples / "raw datalens speed output", use_qch=False)
    assert report.status == run.IMPORTED
    got = {s.sitenumber: (s.cams, s.events) for s in report.sites}
    assert got[17408305] == ([1, 2, 3, 4, 5], 67712) and got[17650202] == ([1, 2], 6038)
    assert "Cam 1 dead (100 % zero speeds)" in report.sites[3].warnings


def test_gate_version_names_import_and_two_versions_of_one_cam_are_refused(tmp_path, proj):
    """0.1.2: <sitenumber>_cam-<N>_speed_<gate version>.csv. The gate version is kept in the file
    name only; two gate versions of the same cam are never imported together."""
    folder = tmp_path / "gates"
    folder.mkdir()
    rows = helpers.speed_rows("NB", [20, 25, 31])
    helpers.write_csv(folder, 17790401, 1, rows).rename(folder / "17790401_cam-1_speed_speed-ns-edit.csv")
    helpers.write_csv(folder, 17790401, 2, rows).rename(folder / "17790401_cam-2_speed_sp.csv")
    helpers.write_csv(folder, 17790401, 3, rows)                                   # an old name beside them
    report = run.import_folder(proj, folder, use_qch=False)
    assert report.status == run.IMPORTED and report.unmatched == []
    assert report.sites[0].files == ["17790401_cam-1_speed_speed-ns-edit.csv", "17790401_cam-2_speed_sp.csv",
                                     "17790401_cam-3_speed.csv"]
    conn = db.open_db(proj.folder, proj.number)
    try:
        stored = [r[0] for r in conn.execute("SELECT file_name FROM speed_cam ORDER BY cam")]
    finally:
        conn.close()
    assert stored == report.sites[0].files                                         # what the camera rows show

    helpers.write_csv(folder, 17790401, 1, rows).rename(folder / "17790401_cam-1_speed_speed-ns.csv")
    twice = run.import_folder(proj, folder, use_qch=False)
    assert twice.status == run.REFUSED
    assert "17790401_cam-1_speed_speed-ns-edit.csv" in twice.message and "17790401_cam-1_speed_speed-ns.csv" in twice.message
    picked = run.import_folder(proj, folder, use_qch=False, only=["17790401_cam-1_speed_speed-ns.csv"])
    assert picked.status == run.IMPORTED and picked.sites[0].files == ["17790401_cam-1_speed_speed-ns.csv"]
