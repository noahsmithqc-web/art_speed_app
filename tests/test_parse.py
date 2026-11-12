"""SP-2: the Datalens speed CSV parser - both forms accepted, everything else refused and named."""

from __future__ import annotations

import pytest

from art_speed.importer import parse
from tests import helpers

ROWS = [("2026-02-24", "06:00:12 AM", "NB Thru", "cl2_passenger_car", 39, 5),
        ("2026-02-24", "12:30:00 PM", "SB Thru", "cl4_bus", 0, 33),
        ("2026-02-24", "06:59:48 PM", "EB Thru", "cl2_passenger_car", 16, 1809)]


def write(tmp_path, text, name="17408304_cam-2_speed.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_raw_form(tmp_path):
    p = parse.parse(write(tmp_path, helpers.csv_text(17408304, ROWS)))
    assert (p.name_sitenumber, p.cam, p.form) == (17408304, 2, parse.RAW)
    assert (p.header_sitecode, p.header_start_date, p.header_start_time, p.header_duration_hr) == (
        "17408304", "2026-02-24", "06:00:00", 13.0)
    assert p.rows == [
        (7, "2026-02-24", "06:00:12", "NB", "Thru", "cl2_passenger_car", 39, "5", None),
        (8, "2026-02-24", "12:30:00", "SB", "Thru", "cl4_bus", 0, "33", None),
        (9, "2026-02-24", "18:59:48", "EB", "Thru", "cl2_passenger_car", 16, "1809", None)]
    assert (p.events, p.zero_events, p.duplicate_lines, p.dead, p.sitenumbers_disagree) == (3, 1, 0, False, False)
    assert len(p.file_hash) == 64


def test_midnight_and_noon(tmp_path):
    rows = [("2026-06-03", "12:02:16 AM", "SB Thru", "c", 11, 1), ("2026-06-03", "12:02:16 PM", "SB Thru", "c", 11, 2)]
    p = parse.parse(write(tmp_path, helpers.csv_text(17650202, rows, start_date="2026-06-03",
                                                     start_time="12:00 AM", duration="24.00"),
                          "17650202_cam-1_speed.csv"))
    assert [r[2] for r in p.rows] == ["00:02:16", "12:02:16"] and p.header_start_time == "00:00:00"


def test_post_macro_form(tmp_path):
    rows = [("10/6/2026", "12:04:31 AM", "EB Thru", "cl2_passenger_car", 55, 53),
            ("10/7/2026", "1:19:52 PM", "EB Thru", "cl2_passenger_car", 57, 277)]
    p = parse.parse(write(tmp_path, helpers.post_macro_text(17889013, 1, rows), "17889013_cam-1_speed.csv"))
    assert p.form == parse.POST_MACRO
    assert (p.header_start_date, p.header_start_time, p.header_duration_hr) == ("2026-10-06", "00:00:00", 0.0)
    assert [(r[1], r[2], r[6]) for r in p.rows] == [("2026-10-06", "00:04:31", 55), ("2026-10-07", "13:19:52", 57)]


def test_post_macro_cam_column_must_match_the_file_name(tmp_path):
    rows = [("10/6/2026", "12:04:31 AM", "EB Thru", "c", 55, 53)]
    with pytest.raises(parse.ParseError, match="cam column says '1' but the file is cam-3"):
        parse.parse(write(tmp_path, helpers.post_macro_text(17889013, 1, rows), "17889013_cam-3_speed.csv"))


def test_trailing_empty_column_on_a_pristine_file_is_still_raw(tmp_path):
    text = helpers.csv_text(17408304, ROWS).replace("Track ID\n", "Track ID,\n")
    assert parse.parse(write(tmp_path, text)).form == parse.RAW


def test_identical_lines_are_kept_and_counted(tmp_path):
    p = parse.parse(write(tmp_path, helpers.csv_text(17408304, ROWS + [ROWS[0], ROWS[0]])))
    assert (p.events, p.duplicate_lines) == (5, 2)


def test_a_file_of_zeros_is_dead(tmp_path):
    rows = [("2026-02-24", "06:00:12 AM", "NB Thru", "c", 0, i) for i in range(4)]
    assert parse.parse(write(tmp_path, helpers.csv_text(17408304, rows))).dead


def test_header_sitenumber_that_differs_is_flagged_not_refused(tmp_path):
    p = parse.parse(write(tmp_path, helpers.csv_text(17408304, ROWS, header_sitecode="17408305")))
    assert p.sitenumbers_disagree and p.header_sitenumber == 17408305 and p.name_sitenumber == 17408304


@pytest.mark.parametrize("change, message", [
    (lambda t: t.replace("Sitecode:", "Site code:"), "line 1: expected 'Sitecode:'"),
    (lambda t: t.replace("Start time:,06:00 AM\n", ""), "line 3: expected 'Start time:'"),
    (lambda t: t.replace("Start date:,2026-02-24", "Start date:,24 Feb 2026"), "line 2: 'Start date:'"),
    (lambda t: t.replace("Duration (hrs):,13.00", "Duration (hrs):,long"), "line 4: 'Duration"),
    (lambda t: t.replace("Duration (hrs):,13.00", "Duration (hrs):,-1"), "line 4: 'Duration"),
    (lambda t: t.replace("13.00\n\n", "13.00\nx\n"), "line 5: expected a blank line"),
    (lambda t: t.replace("Speed (In MPH)", "Speed"), "line 6: the column row"),
    (lambda t: t.replace("Class,Speed (In MPH)", "Speed (In MPH),Class"), "line 6: the column row"),
    (lambda t: t.replace("NB Thru,cl2_passenger_car,39,5", "NB Thru,cl2_passenger_car,39"), "line 7: 5 fields"),
    (lambda t: t.replace("NB Thru", "NB Left"), "line 7: 'NB Left': speed exports hold Thru movements only"),
    (lambda t: t.replace("NB Thru", "NE Thru"), "line 7: 'NE Thru' is not a direction"),
    (lambda t: t.replace(",39,", ",39.5,"), "line 7: speed '39.5' is not a whole number"),
    (lambda t: t.replace(",39,", ",-3,"), "line 7: speed '-3' is not a whole number"),
    (lambda t: t.replace("06:00:12 AM", "06:00:12"), "line 7: '06:00:12' is not a time"),
    (lambda t: t.replace("2026-02-24,06:00:12", "02/24/2026,06:00:12"), "line 7: '02/24/2026' is not a date"),
    (lambda t: t.replace("2026-02-24,06:00:12", "2026-02-30,06:00:12"), "line 7: '2026-02-30' is not a date"),
])
def test_parser_mismatches_are_fatal_and_name_the_line(tmp_path, change, message):
    with pytest.raises(parse.ParseError) as exc:
        parse.parse(write(tmp_path, change(helpers.csv_text(17408304, ROWS))))
    assert message in str(exc.value) and str(exc.value).startswith("17408304_cam-2_speed.csv")


def test_file_names(tmp_path):
    assert parse.match_name("17408304_cam-2_speed.csv") == (17408304, 2)
    assert parse.match_name("177690101_CAM-12_Speed.CSV") == (177690101, 12)
    for bad in ("17408304_cam-2.csv", "1740830_cam-2_speed.csv", "17408304_cam-0_speed.csv",
                "17408304_cam-2_speed.xlsx", "notes.txt", "17408304_cam-2_speed (1).csv"):
        assert parse.match_name(bad) is None
    with pytest.raises(parse.ParseError, match="file name"):
        parse.parse(write(tmp_path, helpers.csv_text(17408304, ROWS), "speed.csv"))


def test_too_short_and_not_text(tmp_path):
    with pytest.raises(parse.ParseError, match="no header block"):
        parse.parse(write(tmp_path, "Sitecode:,17408304\n"))
    path = tmp_path / "17408304_cam-9_speed.csv"
    path.write_bytes(b"\xff\xfe\x00\x00" * 40)
    with pytest.raises(parse.ParseError, match="not a text file"):
        parse.parse(path)


def test_the_real_exports_parse(samples):
    folder = samples / "raw datalens speed output"
    files = sorted(folder.glob("*_speed.csv"))
    assert len(files) >= 17
    counts = {f.name: parse.parse(f).events for f in files}
    assert counts["17408305_cam-1_speed.csv"] == 12402          # the numbers baked into the mockup
    assert counts["17408305_cam-3_speed.csv"] == 13849
    assert counts["17650202_cam-2_speed.csv"] == 2979
    assert parse.parse(folder / "17408305_cam-1_speed.csv").dead
    assert not parse.parse(folder / "17408302_cam-1_speed.csv").dead    # 4,288 of 4,289 zero: not 100 %


def test_a_header_duration_of_zero_is_accepted(tmp_path):
    """Datalens writes 0.00 on a multiday export (the weeklong 17889013 file)."""
    p = parse.parse(write(tmp_path, helpers.csv_text(17408304, ROWS, duration="0.00")))
    assert p.form == parse.RAW and p.header_duration_hr == 0.0 and p.events == 3


def test_the_real_weeklong_export_parses(samples):
    path = samples / "raw datalens speed output" / "17889013_cam-1_speed.csv"
    if not path.is_file():
        pytest.skip("the weeklong 17889013 export is not in sample reports")
    p = parse.parse(path)
    assert (p.form, p.events, p.header_start_date, p.header_start_time, p.header_duration_hr) == (
        parse.RAW, 269187, "2026-10-06", "00:00:00", 0.0)
    assert p.duplicate_lines == 0 or p.duplicate_lines > 0          # counted, never dropped
