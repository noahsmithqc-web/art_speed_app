"""SP-5 follow-up: Excel calculates and saves the generated workbooks, so they are ready to send.

Most of this runs anywhere with a stand-in for Excel. The last test drives the real Excel and
runs only on a Windows computer that has it - there it proves the whole chain: the tool writes
the workbook, Excel calculates it, and the stored Summary equals the engine's numbers.
"""

from __future__ import annotations

import base64
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace

import openpyxl
import pytest

from art_speed import excel, loader, project, report
from art_speed.importer import run
from art_speed.storage import db, store
from tests import helpers


def fake_excel(saves=None, code=0, stderr=""):
    """A stand-in for subprocess.run: 'saves' the listed files (default: all of them)."""
    calls = []

    def runner(cmd, **kwargs):
        files = kwargs["env"][excel.ENV_FILES].split("\n")
        calls.append((cmd, files, kwargs))
        done = files if saves is None else [f for f in files if any(f.endswith(s) for s in saves)]
        return SimpleNamespace(returncode=code, stdout="".join(f"OK {f}\n" for f in done), stderr=stderr)
    runner.calls = calls
    return runner


def test_the_command_is_powershell_with_the_script_encoded():
    cmd = excel.command()
    assert cmd[0] == "powershell.exe" and "-NonInteractive" in cmd and cmd[-2] == "-EncodedCommand"
    script = base64.b64decode(cmd[-1]).decode("utf-16-le")
    assert "Excel.Application" in script and "CalculateFull()" in script and "$wb.Save()" in script
    assert "$excel.Visible = $false" in script and "$excel.DisplayAlerts = $false" in script
    assert "$excel.Quit()" in script                                   # Excel is always closed again
    # the header becomes values after calculating and before saving (2026-11-12)
    assert "'Summary' = 'C2:H13'" in script and script.count("'A1:J5'") == 4
    assert script.index("CalculateFull()") < script.index("Set-HeaderValues $wb") < script.index("$wb.Save()")


def test_every_workbook_of_the_run_goes_to_one_excel(tmp_path):
    a, b = tmp_path / "speed_1.xlsx", tmp_path / "speed_2.xlsx"
    runner = fake_excel()
    done, message = excel.finalize([a, b], runner=runner, is_windows=True)
    assert done and message == "Calculated and saved by Excel: ready to send."
    (cmd, files, kwargs), = runner.calls                               # one Excel for the whole run
    assert files == [str(a.resolve()), str(b.resolve())]
    assert kwargs["timeout"] == excel.START_TIMEOUT_S + excel.PER_FILE_TIMEOUT_S
    assert excel.finalize([], runner=runner, is_windows=True) == (True, "") and len(runner.calls) == 1


def test_when_excel_cannot_do_it_the_answer_says_what_to_do(tmp_path):
    paths = [tmp_path / "speed_1.xlsx", tmp_path / "speed_2.xlsx"]
    done, message = excel.finalize(paths, runner=fake_excel(), is_windows=False)
    assert not done and "not a Windows computer" in message and excel.NOT_DONE in message
    done, message = excel.finalize(paths, runner=fake_excel(saves=[], code=1, stderr="no Excel"), is_windows=True)
    assert not done and "is Excel installed?" in message and excel.NOT_DONE in message
    done, message = excel.finalize(paths, runner=fake_excel(saves=["speed_1.xlsx"], code=1), is_windows=True)
    assert not done and "1 of 2 workbooks; not speed_2.xlsx" in message

    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    done, message = excel.finalize(paths, runner=slow, is_windows=True)
    assert not done and "did not finish within" in message

    def absent(cmd, **kwargs):
        raise FileNotFoundError("powershell.exe")
    done, message = excel.finalize(paths, runner=absent, is_windows=True)
    assert not done and "could not be started" in message


@pytest.fixture
def ready(tmp_path):
    """17408304 imported and Ready: NB and SB from cam 1, posted 25."""
    folder = tmp_path / "project"
    folder.mkdir()
    proj = project.open_folder(folder, "174083")
    csvs = tmp_path / "csv"
    csvs.mkdir()
    helpers.write_csv(csvs, 17408304, 1,
                      helpers.speed_rows("NB", [0, 4, 20, 22, 24, 26, 28, 30, 32, 60]) +
                      helpers.speed_rows("NB", [21, 23], hour=7, first_track=100) +
                      helpers.speed_rows("SB", [0, 18, 26, 31], first_track=200))
    loc = run.import_folder(proj, csvs, use_qch=False).sites[0].location_id
    conn = db.open_db(proj.folder, proj.number)
    with db.transaction(conn):
        conn.execute("UPDATE speed_direction SET posted_mph = 25, source_sitenumber = 17408304, source_cam = 1 "
                     "WHERE direction IN ('NB', 'SB')")
        conn.execute("UPDATE speed_direction SET include_in_report = 0 WHERE direction IN ('EB', 'WB')")
        store.set_manual_site(conn, 17408304, street_ns="Benning Rd SE", street_ew="A St SE")
    conn.close()
    return proj, loc


def test_generate_reports_whether_excel_finished(ready):
    proj, loc = ready
    seen = []

    def finished(paths):
        seen.extend(paths)
        return True, "Calculated and saved by Excel: ready to send."
    out = report.generate(proj, [loc], now=datetime(2026, 11, 12, 8, 0), finalize=finished)
    assert out["excel"] == {"done": True, "message": "Calculated and saved by Excel: ready to send."}
    assert [p.name for p in seen] == ["speed_17408304.xlsx"]
    conn = db.open_db(proj.folder, proj.number)
    try:
        assert loader.drawer(conn, loc)["paper_trail"][0]["what"] == (
            "Report generated: reports_2026_11_12__0800\\speed_17408304.xlsx")
    finally:
        conn.close()
    out = report.generate(proj, [loc], now=datetime(2026, 11, 12, 8, 1),
                          finalize=lambda paths: (False, "Excel could not be started. " + excel.NOT_DONE))
    assert out["excel"]["done"] is False and excel.NOT_DONE in out["excel"]["message"]
    assert (proj.folder / out["folder"] / "speed_17408304.xlsx").is_file()      # the workbook is still written


def test_the_real_excel_calculates_the_generated_workbook(ready):
    """Windows with Excel only. The tool writes the workbook, the real Excel calculates and saves
    it, and what is then stored in the file is the engine's 85th percentile and % over posted."""
    if sys.platform != "win32":
        pytest.skip("needs Windows and Excel")
    proj, loc = ready
    out = report.generate(proj, [loc], now=datetime(2026, 11, 12, 8, 0))         # the real excel.finalize
    if not out["excel"]["done"]:
        pytest.skip(f"Excel could not be run here: {out['excel']['message']}")
    conn = db.open_db(proj.folder, proj.number)
    try:
        want = loader.drawer(conn, loc)["directions"]
    finally:
        conn.close()
    path = proj.folder / out["folder"] / "speed_17408304.xlsx"
    stored = openpyxl.load_workbook(path, data_only=True)["Summary"]             # values as stored, no calculation
    assert stored["E3"].value == "Benning Rd SE  -  A St SE"
    for d, column in (("NB", "E"), ("SB", "F")):
        assert stored[f"{column}11"].value == pytest.approx(want[d]["report"]["p85"])
        assert stored[f"{column}12"].value == pytest.approx(want[d]["report"]["pct_over_posted"])
        assert stored[f"{column}13"].value == 25
    assert stored["G11"].value == "--" and stored["H11"].value == "--"
    book = openpyxl.load_workbook(path)
    formulas = book["Summary"]
    assert len(formulas._charts) == 5                                             # Excel kept the template's charts
    # the header is values now: no formula left in Summary C2:H13 or in A1:J5 of an included direction sheet
    for sheet, cells in ((formulas, "C2:H13"), (book["NB"], "A1:J5"), (book["SB"], "A1:J5")):
        left = [c.coordinate for row in sheet[cells] for c in row if isinstance(c.value, str) and c.value.startswith("=")]
        assert left == [], f"{sheet.title}: formulas left in {left}"
    assert formulas["E11"].value == pytest.approx(want["NB"]["report"]["p85"])
    assert str(formulas["E14"].value).startswith("=IFERROR(")                     # below the header the sheet stays live
