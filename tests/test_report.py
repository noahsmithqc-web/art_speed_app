"""SP-5: the report writer - a fill of the delivered template, header fields and source-cam rows only."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from datetime import datetime, time
from xml.etree import ElementTree

import openpyxl
import pytest
from fastapi.testclient import TestClient

from art_speed import loader, parity, project, report, server
from art_speed.importer import run
from art_speed.storage import db, store
from tests import helpers


@pytest.fixture
def proj(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    return project.open_folder(folder, "174083")


@pytest.fixture
def ready(proj, tmp_path):
    """17408304 imported and Ready: NB and SB from cam 1 (posted 25), EB and WB not in the report."""
    csvs = tmp_path / "csv"
    csvs.mkdir()
    helpers.write_csv(csvs, 17408304, 1,
                      helpers.speed_rows("NB", [0, 4, 20, 22, 24, 26, 28, 30, 32, 60]) +
                      helpers.speed_rows("NB", [21, 23], hour=7, first_track=100) +
                      helpers.speed_rows("SB", [0, 18, 26], first_track=200) +
                      helpers.speed_rows("EB", [15], first_track=300))
    loc = run.import_folder(proj, csvs, use_qch=False).sites[0].location_id
    conn = db.open_db(proj.folder, proj.number)
    with db.transaction(conn):
        conn.execute("UPDATE speed_direction SET posted_mph = 25, source_sitenumber = 17408304, source_cam = 1 "
                     "WHERE direction IN ('NB', 'SB')")
        conn.execute("UPDATE speed_direction SET include_in_report = 0 WHERE direction IN ('EB', 'WB')")
        store.set_manual_site(conn, 17408304, street_ns="Benning Rd SE", street_ew="A St SE",
                              latitude=38.8884457, longitude=-76.9369868)
    conn.close()
    return proj, loc


def change(proj, sql, params=()):
    conn = db.open_db(proj.folder, proj.number)
    with db.transaction(conn):
        conn.execute(sql, params)
    conn.close()


@pytest.fixture(autouse=True)
def no_excel(monkeypatch):
    """These tests read what the tool itself wrote, so Excel is not run on the workbooks here
    (tests/test_excel.py covers that step, with the real Excel where there is one)."""
    monkeypatch.setattr(report.excel, "finalize", lambda paths, **kw: (False, "Excel was not run in this test."))


def generate_one(proj, loc):
    out = report.generate(proj, [loc], now=datetime(2026, 11, 11, 14, 30))
    return proj.folder / out["folder"] / out["files"][0]["file"], out


# ------------------------------------------------------------------ names

def test_workbook_names_follow_the_slices():
    assert report.file_name([{"sitenumber": 17408304}]) == "speed_17408304.xlsx"
    assert report.file_name([{"sitenumber": 17408301}, {"sitenumber": 17408302}]) == "speed_17408301_17408302.xlsx"
    assert report.file_name([{"sitenumber": 17889013, "date": "2026-10-07"}]) == "speed_17889013_2026-10-07.xlsx"
    assert report.file_name([{"sitenumber": 17889013, "date": "2026-10-07", "from_hour": 6, "to_hour": 10}]) == (
        "speed_17889013_2026-10-07_0600-1000.xlsx")
    assert report.file_name([{"sitenumber": 17889013, "from_hour": 15, "to_hour": 19}]) == "speed_17889013_1500-1900.xlsx"


def test_reports_folder_is_timestamped_and_never_reused(tmp_path):
    now = datetime(2026, 11, 11, 14, 30)
    first = report.reports_folder(tmp_path, now)
    assert first.name == "reports_2026_11_11__1430"
    first.mkdir()
    assert report.reports_folder(tmp_path, now).name == "reports_2026_11_11__1430_2"


# ------------------------------------------------------------------ what is written

def test_only_the_header_fields_and_the_rows_are_written(ready):
    proj, loc = ready
    change(proj, "UPDATE speed_location SET note = 'Queueing NB & <low> speeds.'")
    path, out = generate_one(proj, loc)
    assert path.name == "speed_17408304.xlsx" and out["folder"] == "reports_2026_11_11__1430"
    wb = openpyxl.load_workbook(path)
    assert [(ws.title, ws.sheet_state) for ws in wb] == [
        ("Summary", "visible"), ("NB", "visible"), ("SB", "visible"), ("EB", "hidden"), ("WB", "hidden"),
        ("Locations", "hidden"), ("summary_stats", "hidden")]
    s = wb["Summary"]
    assert (s["E2"].value, s["E5"].value, s["E6"].value) == (17408304, time(6, 0), 13)
    assert s["E5"].number_format == "h:mm AM/PM"                       # the template's own cell format
    # the template's formulas are all still there
    assert (s["E3"].value, s["E4"].value) == ("=NB!B2", "=NB!B3")
    assert [s[f"{c}11"].value for c in "EFGH"] == ["=NB!H5", "=SB!H5", "=EB!H5", "=WB!H5"]
    assert [s[f"{c}12"].value for c in "EFGH"] == ["=NB!J2", "=SB!J2", "--", "--"]
    assert [s[f"{c}13"].value for c in "EFGH"] == [
        "=_xlfn.XLOOKUP(E2,Locations!A:A,Locations!D:D)", "=E13", "--", "--"]
    assert s["E14"].value == '=IFERROR(IF($G$20>0,E13+$G$20,E13),"--")' and s["G20"].value is None      # the threshold input is the client's
    # template of 2026-11-12: % over posted shows two decimals, on Summary and the direction sheets
    assert s["E12"].number_format == "0.00%" and wb["NB"]["J2"].number_format == "0.00%"
    assert s["C16"].value == "Note: Queueing NB & <low> speeds."
    assert len(s._charts) == 5 and len(s._images) == 1
    assert [c.value for c in wb["Locations"][2]] == [
        17408304, "Benning Rd SE  -  A St SE", datetime(2026, 2, 24), 25, None, "Queueing NB & <low> speeds.",
        '=I2&", "&J2', None, 38.8884457, -76.9369868]
    assert all(c.value is None for row in wb["Locations"].iter_rows(min_row=3) for c in row)
    nb = wb["NB"]
    assert nb["B1"].value == "=Summary!E2" and nb["H5"].value == '=IFERROR(_xlfn.PERCENTILE.INC(E:E,0.85),"--")'
    assert nb["J2"].value == "=summary_stats!D34"
    rows = [[c.value for c in r[:7]] for r in nb.iter_rows(min_row=8)]
    # the source cam's NB rows: the zero and the 4 (below the 5 mph floor) are left out
    assert [r[4] for r in rows] == [20, 22, 24, 26, 28, 30, 32, 60, 21, 23]
    assert rows[0] == [datetime(2026, 2, 24), time(6, 0, 2), "NB Thru", "cl2_passenger_car", 20, 3, "06"]
    assert rows[-1][1:] == [time(7, 0, 1), "NB Thru", "cl2_passenger_car", 23, 101, "07"]
    assert nb["A8"].number_format == "mm-dd-yy" and "h:mm:ss" in nb["B8"].number_format
    assert nb.column_dimensions["G"].hidden
    assert [r[0].value for r in wb["SB"].iter_rows(min_row=8)] == [datetime(2026, 2, 24)] * 2
    assert wb["EB"].max_row == 7 and wb["WB"].max_row == 7             # not in the report: header only, no rows
    assert wb["summary_stats"]["D34"].value == '=IFERROR(COUNTIFS(NB!$E:$E,">"&$D$33)/COUNT(NB!$E:$E),0)'


def test_every_other_part_of_the_template_is_untouched(ready):
    proj, loc = ready
    path, _ = generate_one(proj, loc)
    made, template = zipfile.ZipFile(path), zipfile.ZipFile(report.TEMPLATE)
    changed = {n for n in template.namelist() if n not in made.namelist() or made.read(n) != template.read(n)}
    assert changed == {"[Content_Types].xml", "xl/_rels/workbook.xml.rels", "xl/calcChain.xml", "xl/sharedStrings.xml",
                       "xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml",
                       "xl/worksheets/sheet3.xml", "xl/worksheets/sheet6.xml"}
    assert set(made.namelist()) == set(template.namelist()) - {"xl/calcChain.xml"}
    for name in template.namelist():                                   # every chart, drawing, style, theme, image
        if name.startswith(("xl/charts/", "xl/drawings/", "xl/media/", "xl/theme/")) or name == "xl/styles.xml":
            assert made.read(name) == template.read(name), name
    assert made.read("xl/worksheets/sheet7.xml") == template.read("xl/worksheets/sheet7.xml")     # summary_stats
    for name in made.namelist():                                       # and what was rewritten is well-formed
        if name.endswith((".xml", ".rels")):
            ElementTree.fromstring(made.read(name))
    workbook = made.read("xl/workbook.xml").decode()
    assert 'fullCalcOnLoad="1"' in workbook and "calcChain" not in made.read("[Content_Types].xml").decode()
    strings = made.read("xl/sharedStrings.xml").decode()
    assert len(ElementTree.fromstring(strings)) == int(strings.split('uniqueCount="')[1].split('"')[0])


def test_masks_adjustment_and_slices_decide_the_rows(ready):
    proj, loc = ready
    conn = db.open_db(proj.folder, proj.number)
    with db.transaction(conn):
        conn.execute("UPDATE speed_direction SET mask_low = NULL, mask_high = 40, adjust_by = -2 WHERE direction = 'NB'")
        store.set_masked(conn, 17408304, 1, ["2026-02-24 07"], True)
    want = loader.drawer(conn, loc)["directions"]["NB"]["report"]["n"]
    conn.close()
    path, _ = generate_one(proj, loc)
    speeds = [r[4].value for r in openpyxl.load_workbook(path)["NB"].iter_rows(min_row=8)]
    assert speeds == [2, 18, 20, 22, 24, 26, 28, 30] and len(speeds) == want     # 4 kept, 60 out, hour 07 out, all -2


def test_a_second_direction_alone_keeps_its_axis_posted_speed(ready):
    proj, loc = ready
    change(proj, "UPDATE speed_direction SET include_in_report = 0 WHERE direction = 'NB'")
    path, _ = generate_one(proj, loc)
    wb = openpyxl.load_workbook(path)
    s = wb["Summary"]
    assert [s[f"{c}12"].value for c in "EF"] == ["--", "=SB!J2"]
    assert [s[f"{c}13"].value for c in "EF"] == ["=_xlfn.XLOOKUP(E2,Locations!A:A,Locations!D:D)", "=E13"]
    assert wb["Locations"]["D2"].value == 25 and wb["NB"].sheet_state == "hidden"


# ------------------------------------------------------------------ refusals

def test_refused_until_ready(ready):
    proj, loc = ready
    change(proj, "UPDATE speed_direction SET posted_mph = NULL WHERE direction = 'SB'")
    with pytest.raises(report.ReportError, match="Location 04 is not Ready: SB has no posted speed"):
        report.generate(proj, [loc])
    with pytest.raises(report.ReportError, match="No location is Ready"):
        report.generate(proj)
    assert not list(proj.folder.glob("reports_*"))


def test_refused_when_an_axis_has_two_posted_speeds(ready):
    proj, loc = ready
    change(proj, "UPDATE speed_direction SET posted_mph = 30 WHERE direction = 'SB'")
    with pytest.raises(report.ReportError, match="NB is posted 25 and SB 30. The report template has one posted speed per axis"):
        report.generate(proj, [loc])


def test_refused_without_a_name_and_for_an_unknown_location(ready):
    proj, loc = ready
    change(proj, "UPDATE speed_qch_site SET street_ns = NULL, street_ew = NULL")
    with pytest.raises(report.ReportError, match="has no name"):
        report.generate(proj, [loc])
    change(proj, "UPDATE speed_location SET name_source = 'manual', manual_name = 'Benning & A (13h)'")
    path, _ = generate_one(proj, loc)
    assert openpyxl.load_workbook(path)["Locations"]["B2"].value == "Benning & A (13h)"
    with pytest.raises(report.ReportError, match="Location 999 does not exist"):
        report.generate(proj, [999])


def test_a_run_is_all_or_nothing_and_logged(ready, tmp_path):
    proj, loc = ready
    csvs = tmp_path / "more"
    csvs.mkdir()
    helpers.write_csv(csvs, 17408305, 1, helpers.speed_rows("NB", [30, 31]))
    other = run.import_folder(proj, csvs, use_qch=False).sites[0].location_id       # not Ready
    with pytest.raises(report.ReportError, match="Location 05 is not Ready"):
        report.generate(proj, [loc, other])
    assert not list(proj.folder.glob("reports_*"))
    out = report.generate(proj, now=datetime(2026, 11, 11, 14, 30))                 # every Ready location: just 04
    assert [f["code"] for f in out["files"]] == ["04"]
    again = report.generate(proj, now=datetime(2026, 11, 11, 14, 30))
    assert again["folder"] == "reports_2026_11_11__1430_2"                          # every run kept
    conn = db.open_db(proj.folder, proj.number)
    try:
        assert conn.execute("SELECT COUNT(*) FROM speed_report_log").fetchone()[0] == 2
        drawer = loader.drawer(conn, loc)
        assert drawer["paper_trail"][0]["what"] == ("Report generated: reports_2026_11_11__1430_2\\speed_17408304.xlsx"
                                                    " (not calculated by Excel: open and save it before sending)")
        assert loader.tab(conn)["summary"]["last_report"] is not None
    finally:
        conn.close()


def test_generate_through_the_api(ready):
    proj, loc = ready
    client = TestClient(server.create_app(proj))
    out = client.post("/speed/report", json={"ids": [loc]}).json()
    assert out["files"] == [{"location_id": loc, "code": "04", "file": "speed_17408304.xlsx"}]
    assert (proj.folder / out["folder"] / "speed_17408304.xlsx").is_file()
    assert out["tab"]["summary"]["last_report"] is not None
    assert len(client.post("/speed/report", json={}).json()["files"]) == 1          # no ids: every Ready location
    client.patch(f"/speed/locations/{loc}/directions/NB", json={"source_cam": None})
    r = client.post("/speed/report", json={"ids": [loc]})
    assert r.status_code == 422 and "not Ready: NB has no source cam" in r.json()["message"]
    assert client.post("/speed/report", json={"ids": "all"}).status_code == 422


# ------------------------------------------------------------------ parity with the delivered workbooks

@pytest.fixture(scope="module")
def delivered_project(samples, tmp_path_factory):
    """The real exports imported, the delivered decisions applied, and a report generated for all six."""
    folder = tmp_path_factory.mktemp("delivered")
    proj = project.open_folder(folder, "174083")
    imported = run.import_folder(proj, samples / "raw datalens speed output", use_qch=False)
    books = {b.sitenumber: b for b in map(parity.read_delivered, parity.delivered_files(samples))}
    conn = db.open_db(proj.folder, proj.number)
    with db.transaction(conn):
        for s in imported.sites:
            sn = str(s.sitenumber)
            if sn not in books:                 # an export with no delivered workbook (the weeklong 17889013)
                continue
            for d in parity.DIRECTIONS:
                cam, floor = parity.DELIVERED.get((sn, d), (None, None))
                conn.execute(
                    "UPDATE speed_direction SET include_in_report = ?, posted_mph = ?, source_sitenumber = ?, "
                    "source_cam = ?, mask_low = ? WHERE location_id = ? AND direction = ?",
                    (int(cam is not None), 25 if cam else None, s.sitenumber if cam else None, cam, floor,
                     s.location_id, d))
            conn.execute("UPDATE speed_location SET name_source = 'manual', manual_name = ? WHERE location_id = ?",
                         (books[sn].location, s.location_id))
        conn.execute("UPDATE speed_cam SET corner = 'NE'")
    conn.close()
    out = report.generate(proj, now=datetime(2026, 11, 11, 14, 30), finalize=lambda paths: (False, "not run here"))
    return proj.folder / out["folder"], books


@pytest.mark.parametrize("sitenumber", ["17408301", "17408302", "17408304", "17408305", "17408306", "17650202"])
def test_the_generated_workbook_holds_the_delivered_header_and_records(delivered_project, sitenumber):
    folder, books = delivered_project
    want = books[sitenumber]
    wb = openpyxl.load_workbook(folder / f"speed_{sitenumber}.xlsx")
    s, loc = wb["Summary"], wb["Locations"]
    assert s["E2"].value == int(sitenumber) and loc["A2"].value == int(sitenumber)
    assert loc["B2"].value == want.location
    assert loc["C2"].value.date().isoformat() == want.start_date           # the count's own date
    assert s["E5"].value.strftime("%H:%M:%S") == want.start_time and s["E6"].value == want.duration_hr
    for d in parity.DIRECTIONS:
        item, ws = want.directions[d], wb[d]
        got = [(r[0].date().isoformat(), r[1].strftime("%H:%M:%S"), r[2], r[3], r[4], r[5], r[6])
               for r in ws.iter_rows(min_row=8, max_col=7, values_only=True)]
        assert got == item.rows, f"{d}: the records differ from the delivered sheet"
        if item.rows:
            assert ws.sheet_state == "visible" and loc["D2" if d in ("NB", "SB") else "E2"].value == item.posted
        else:
            assert ws.sheet_state == "hidden"


def test_excels_formulas_on_the_generated_workbook_give_the_delivered_numbers(delivered_project, tmp_path):
    """The template's own formulas, recalculated on the filled workbook, print the delivered Summary.
    Needs LibreOffice to do the recalculation here; skipped where it is not installed."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        pytest.skip("LibreOffice is not installed (Excel does this recalculation when it opens the workbook)")
    folder, books = delivered_project
    profile = tmp_path / "lo_profile" / "user"
    profile.mkdir(parents=True)
    (profile / "registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse">'
        "<value>0</value></prop></item></oor:items>", encoding="utf-8")
    out = tmp_path / "recalculated"
    try:
        subprocess.run([soffice, f"-env:UserInstallation={(tmp_path / 'lo_profile').as_uri()}", "--headless",
                        "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(out),
                        str(folder / "speed_17408305.xlsx"), str(folder / "speed_17650202.xlsx")],
                       check=True, capture_output=True, timeout=170)
    except (subprocess.SubprocessError, OSError) as exc:
        pytest.skip(f"LibreOffice could not recalculate the workbook here ({type(exc).__name__})")
    for sitenumber in ("17408305", "17650202"):
        want = books[sitenumber]
        s = openpyxl.load_workbook(out / f"speed_{sitenumber}.xlsx", data_only=True)["Summary"]
        assert s["E3"].value == want.location and s["E4"].value.date().isoformat() == want.start_date
        for d, column in zip(parity.DIRECTIONS, "EFGH"):
            item = want.directions[d]
            if item.rows:
                assert s[f"{column}11"].value == pytest.approx(item.p85), f"{sitenumber} {d} 85th"
                assert s[f"{column}12"].value == pytest.approx(item.pct_over_posted, abs=1e-12), f"{sitenumber} {d} % over"
                assert s[f"{column}13"].value == item.posted
            else:
                assert s[f"{column}11"].value == "--" and s[f"{column}12"].value == "--"
