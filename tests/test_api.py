"""SP-4: the router. Each route calls the right thing on the open project, answers in the
contract's shape, logs, and refuses cleanly."""

from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from art_speed import contract, project, server
from tests import helpers
from tests.test_engine_fixtures import differences


@pytest.fixture
def proj(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    return project.open_folder(folder, "174083")


@pytest.fixture
def csvs(tmp_path):
    """17408304: cam 1 and cam 2 over two hours. 17889013: one cam over two days."""
    folder = tmp_path / "csv"
    folder.mkdir()
    helpers.write_csv(folder, 17408304, 1,
                      helpers.speed_rows("NB", [0, 4, 20, 22, 24, 26, 28, 30, 32, 60]) +
                      helpers.speed_rows("NB", [21, 23], hour=7, first_track=100) +
                      helpers.speed_rows("SB", [0, 0, 0, 18], first_track=200))
    helpers.write_csv(folder, 17408304, 2, helpers.speed_rows("SB", [25, 27, 29, 31]) +
                      helpers.speed_rows("NB", [0, 0, 19], first_track=50))
    helpers.write_csv(folder, 17889013, 1,
                      helpers.speed_rows("EB", [40, 41, 42, 55, 61], date="2026-10-06", hour=6) +
                      helpers.speed_rows("EB", [44, 45], date="2026-10-06", hour=16, first_track=20) +
                      helpers.speed_rows("EB", [50, 51, 52], date="2026-10-07", hour=6, first_track=30),
                      start_date="2026-10-06", start_time="12:00 AM", duration="48.00")
    return folder


@pytest.fixture
def client(proj):
    return TestClient(server.create_app(proj))


@pytest.fixture
def loaded(client, csvs):
    """The two sitenumbers imported; returns (client, {sitenumber: location id})."""
    report = client.post("/speed/import", json={"folder": str(csvs), "use_qch": False}).json()
    assert report["status"] == "imported"
    return client, {s["sitenumber"]: s["location_id"] for s in report["sites"]}


def trail(client, location_id) -> list:
    return [t["what"] for t in client.get(f"/speed/locations/{location_id}").json()["paper_trail"]]


def refused(response, status=422) -> str:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["ok"] is False
    return body["message"]


# ------------------------------------------------------------------ reads

def test_no_project_open():
    client = TestClient(server.create_app(None))
    assert "No project folder is open" in refused(client.get("/speed/locations"), 409)
    assert client.get("/speed/version").json()["db_version"] == 1


def test_a_project_with_no_speed_data_has_an_empty_tab(client, proj):
    tab = client.get("/speed/locations").json()
    assert contract.check_tab(tab) == [] and tab["rows"] == [] and tab["generate"] == {"enabled": False, "green": False}
    info = client.get("/speed/project").json()
    assert (info["number"], info["has_speed_data"], info["is_art_project"]) == ("174083", False, False)
    assert not proj.db_path.exists()                               # a read never creates the database
    refused(client.get("/speed/locations/1"), 404)


def test_tab_and_drawer_are_the_contract_shapes(loaded):
    client, ids = loaded
    tab = client.get("/speed/locations").json()
    assert contract.check_tab(tab) == []
    assert [r["code"] for r in tab["rows"]] == ["04", "13"] and tab["summary"]["cams"] == 3
    for location_id in ids.values():
        assert contract.check_drawer(client.get(f"/speed/locations/{location_id}").json()) == []
    assert "does not exist" in refused(client.get("/speed/locations/999"), 404)


# ------------------------------------------------------------------ import and delete

def test_import_asks_then_follows_the_choice(client, csvs):
    helpers.write_csv(csvs, 17408306, 1, helpers.speed_rows("NB", [20]), header_sitecode="17408307")
    asked = client.post("/speed/import", json={"folder": str(csvs), "use_qch": False}).json()
    assert asked["status"] == "needs_choice" and asked["disagreements"][0]["file"] == "17408306_cam-1_speed.csv"
    assert client.get("/speed/locations").json()["rows"] == []
    done = client.post("/speed/import", json={"folder": str(csvs), "use_qch": False,
                                              "choices": {"17408306_cam-1_speed.csv": "file name"}}).json()
    assert done["status"] == "imported" and done["totals"]["sitenumbers"] == 3
    assert "Pick the folder" in refused(client.post("/speed/import", json={"folder": ""}))
    assert "does not take" in refused(client.post("/speed/import", json={"folder": "x", "nope": 1}))
    assert client.post("/speed/import", json={"folder": str(csvs / "missing")}).json()["status"] == "refused"


def test_delete_sitenumber(loaded):
    client, ids = loaded
    out = client.delete("/speed/sitenumbers/17889013").json()
    assert out["deleted"]["events"] == 10 and [r["code"] for r in out["tab"]["rows"]] == ["04"]
    refused(client.delete("/speed/sitenumbers/17889013"), 404)


# ------------------------------------------------------------------ directions

def test_posted_speed_follows_the_axis_link(loaded):
    client, ids = loaded
    loc = ids["17408304"]
    d = client.patch(f"/speed/locations/{loc}/directions/NB", json={"posted": 25}).json()
    assert d["directions"]["NB"]["posted"] == 25 and d["directions"]["SB"]["posted"] == 25      # same both ways
    assert d["paper_trail"][0]["what"] == "Posted speed NB & SB set to 25"
    d = client.patch(f"/speed/locations/{loc}", json={"posted_same": {"NS": False}}).json()
    assert d["posted_same"] == {"NS": False, "EW": True}
    assert d["paper_trail"][0]["what"] == "N/S posted speed unlinked (set each direction)"
    d = client.patch(f"/speed/locations/{loc}/directions/SB", json={"posted": 30}).json()
    assert (d["directions"]["NB"]["posted"], d["directions"]["SB"]["posted"]) == (25, 30)
    assert d["paper_trail"][0]["what"] == "Posted speed SB set to 30"
    d = client.patch(f"/speed/locations/{loc}", json={"posted_same": {"NS": True}}).json()
    assert (d["directions"]["NB"]["posted"], d["directions"]["SB"]["posted"]) == (25, 25)      # NB's value wins
    d = client.patch(f"/speed/locations/{loc}/directions/NB", json={"posted": None}).json()
    assert d["directions"]["SB"]["posted"] is None and d["paper_trail"][0]["what"] == "Posted speed NB & SB set to blank"
    assert "Posted speed must be" in refused(client.patch(f"/speed/locations/{loc}/directions/NB", json={"posted": -5}))
    assert "is not a direction" in refused(client.patch(f"/speed/locations/{loc}/directions/NE", json={"posted": 25}))


def test_source_cam_adjust_and_masks(loaded):
    client, ids = loaded
    loc = ids["17408304"]
    url = f"/speed/locations/{loc}/directions/NB"
    client.patch(url, json={"posted": 25})
    d = client.patch(url, json={"source_cam": "17408304:1"}).json()
    nb = d["directions"]["NB"]
    assert nb["source_cam"] == "17408304:1" and (nb["report"]["n"], nb["report"]["masked"]) == (10, 1)
    assert d["paper_trail"][0]["what"] == "NB source cam set to cam 1"            # no corners yet: no suggestion
    d = client.patch(url, json={"mask": {"low": None, "high": 40}}).json()
    assert d["directions"]["NB"]["mask"] == {"low": None, "high": 40, "is_default": False}
    assert d["directions"]["NB"]["report"]["n"] == 10                              # the 4 is back, the 60 is out
    assert [t["what"] for t in d["paper_trail"][:2]] == ["NB upper mask set to above 40 mph", "NB lower mask off"]
    assert d["histograms"]["17408304:1"]["NB"]["bins10"][-1]["above_upper_mask"] is True
    d = client.patch(url, json={"adjust_by": -2}).json()
    assert d["directions"]["NB"]["report"]["adjust_by"] == -2 and d["paper_trail"][0]["what"] == "NB adjust by set to -2 mph"
    d = client.patch(url, json={"source_cam": "2"}).json()                         # a bare cam number: one sitenumber
    assert d["directions"]["NB"]["mask"] == {"low": 5, "high": None, "is_default": True}
    assert d["paper_trail"][0]["what"] == "NB source cam set to cam 2 · speed mask reset to default"
    assert d["directions"]["NB"]["adjust_by"] == -2                                # adjust-by is not reset
    d = client.patch(url, json={"mask": {"low": 10}}).json()
    d = client.patch(url, json={"mask": {"low": 5}}).json()
    assert d["paper_trail"][0]["what"] == "NB mask reset to below 5 mph"
    assert "below the upper mask" in refused(client.patch(url, json={"mask": {"low": 30, "high": 20}}))
    assert "whole number" in refused(client.patch(url, json={"adjust_by": 1.5}))
    assert "not a cam of" in refused(client.patch(url, json={"source_cam": "17408304:9"}), 404)
    d = client.patch(url, json={"source_cam": None}).json()
    assert d["directions"]["NB"]["report"] is None and d["paper_trail"][0]["what"] == "NB source cam cleared"


def test_include_in_report_and_a_refusal_writes_nothing(loaded):
    client, ids = loaded
    loc = ids["17408304"]
    before = trail(client, loc)
    d = client.patch(f"/speed/locations/{loc}/directions/EB", json={"include_in_report": False}).json()
    assert d["directions"]["EB"]["include_in_report"] is False
    assert d["paper_trail"][0]["what"] == "EB excluded from the report (overrides the QC Hub lane config)"
    refused(client.patch(f"/speed/locations/{loc}/directions/EB", json={"include_in_report": True, "posted": "fast"}))
    after = client.get(f"/speed/locations/{loc}").json()
    assert after["directions"]["EB"]["include_in_report"] is False                 # the whole PATCH was rolled back
    assert len(after["paper_trail"]) == len(before) + 1
    assert "does not take" in refused(client.patch(f"/speed/locations/{loc}/directions/EB", json={"speed": 1}))
    assert "nothing to change" in refused(client.patch(f"/speed/locations/{loc}/directions/EB", json={}))


# ------------------------------------------------------------------ cams

def test_corners_and_the_suggestion(loaded):
    client, ids = loaded
    loc = ids["17408304"]
    d = client.patch(f"/speed/locations/{loc}/cams/17408304:1", json={"corner": "NE"}).json()
    assert d["cams"][0]["corner"] == "NE" and d["paper_trail"][0] | {"when": ""} == {
        "when": "", "what": "Cam 1 corner set to NE", "who": d["paper_trail"][0]["who"], "scope": "cam",
        "sitenumber": "17408304"}
    assert d["directions"]["NB"]["suggested"] is None                             # cam 2 still has no corner
    d = client.patch(f"/speed/locations/{loc}/cams/2", json={"corner": "SW"}).json()
    assert d["directions"]["NB"]["suggested"] == {"cam": "17408304:1", "why": "primary corner"}
    assert d["directions"]["SB"]["suggested"] == {"cam": "17408304:2", "why": "primary corner"}
    d = client.post(f"/speed/locations/{loc}/use-suggestions").json()
    assert [d["directions"][x]["source_cam"] for x in ("NB", "SB", "EB", "WB")] == [
        "17408304:1", "17408304:2", None, None]
    assert d["paper_trail"][0]["what"] == "Suggested source cams applied (2 changes)"
    d = client.post(f"/speed/locations/{loc}/use-suggestions").json()
    assert d["paper_trail"][0]["what"] == "Suggested source cams already in use"
    d = client.patch(f"/speed/locations/{loc}/cams/2", json={"corner": None}).json()
    assert d["cams"][1]["corner"] is None and d["paper_trail"][0]["what"] == "Cam 2 corner cleared"
    assert "is not a corner" in refused(client.patch(f"/speed/locations/{loc}/cams/2", json={"corner": "N"}))


def test_time_mask_from_detail_and_the_over_half_acknowledgement(loaded):
    client, ids = loaded
    loc = ids["17408304"]
    for d in ("NB", "SB"):
        client.patch(f"/speed/locations/{loc}/directions/{d}", json={"source_cam": "17408304:1"})
    url = f"/speed/locations/{loc}/cams/17408304:1"
    assert "nothing to acknowledge" in refused(client.patch(url, json={"acknowledge_mask": True}))
    d = client.patch(url, json={"mask": {"hours": ["2026-02-24 07"], "masked": True}}).json()
    assert d["cams"][0]["masked_hours"] == ["2026-02-24 07"] and d["location"]["masked_hours"] == 1
    assert d["paper_trail"][0]["what"] == "Cam 1: masked 2026-02-24 07:00"
    assert d["hourly"]["directions"]["NB"]["days"][0]["cells"][7]["masked"] is True
    assert d["hourly"]["directions"]["NB"]["shared_with"] == ["SB"]
    d = client.patch(url, json={"mask": {"hours": ["2026-02-24 06"], "masked": True}}).json()
    assert [w["code"] for w in d["warnings"] if w["code"] != "not_in_qch"] == ["over_half_masked"]
    d = client.patch(url, json={"acknowledge_mask": True}).json()
    assert d["cams"][0]["mask_acknowledged"] and "over_half_masked" not in [w["code"] for w in d["warnings"]]
    assert d["paper_trail"][0]["what"] == "Cam 1: over-half time mask acknowledged"
    d = client.patch(url, json={"mask": {"hours": ["2026-02-24 06", "2026-02-24 07"], "masked": False}}).json()
    assert d["cams"][0]["masked_hours"] == [] and not d["cams"][0]["mask_acknowledged"]     # any change clears it
    assert d["paper_trail"][0]["what"] == "Cam 1: unmasked 2026-02-24 06:00–08:00"
    assert "has no data at" in refused(client.patch(url, json={"mask": {"hours": ["2026-02-24 23"], "masked": True}}))
    assert "is not an hour" in refused(client.patch(url, json={"mask": {"hours": ["today"], "masked": True}}))


# ------------------------------------------------------------------ the location

def test_note_name_window_lane_config_and_manual_site(loaded):
    client, ids = loaded
    loc = ids["17408304"]
    url = f"/speed/locations/{loc}"
    d = client.patch(url, json={"note": " Queueing NB. "}).json()
    assert d["note"] == "Queueing NB." and d["paper_trail"][0]["what"] == "Report note set"
    assert "Type the manual" in refused(client.patch(url, json={"name_source": "manual"}))
    d = client.patch(url, json={"name_source": "manual", "manual_name": "Benning Rd SE  -  A St SE (13h)"}).json()
    assert (d["location"]["name"], d["location"]["name_source"]) == ("Benning Rd SE  -  A St SE (13h)", "manual")
    d = client.patch(url, json={"name_source": "qch"}).json()
    assert d["location"]["name"] == "" and d["location"]["manual_name"] == "Benning Rd SE  -  A St SE (13h)"
    d = client.patch(url, json={"window": {"start_time": "7:00 am", "duration_hours": 1}}).json()
    assert d["location"]["window"] == {"start_date": "2026-02-24", "start_time": "07:00 AM",
                                       "duration_hours": 1, "source": "edited"}
    assert {"code": "outside_window", "text": "1 hour of data outside the count window"} in d["warnings"]
    assert "YYYY-MM-DD" in refused(client.patch(url, json={"window": {"start_date": "2/24/2026"}}))
    client.patch(f"{url}/directions/EB", json={"include_in_report": False})
    client.patch(url, json={"lane_config": {"EB": "0000000"}})
    d = client.patch(url, json={"lane_config": {"EB": "6200000"}}).json()
    assert (d["directions"]["EB"]["lane_config"], d["directions"]["EB"]["lane_config_qch"],
            d["directions"]["EB"]["exists"], d["directions"]["EB"]["include_in_report"]) == (
        "6200000", "2000000", True, True)
    assert d["paper_trail"][0]["what"] == ("EB lane config 0000000 → 6200000 (QC Hub: 2000000) · EB now "
                                           "exists and is in the report")
    assert "up to seven digits" in refused(client.patch(url, json={"lane_config": {"EB": "89"}}))
    d = client.patch(url, json={"site": {"street_ns": "Benning Rd SE", "street_ew": "A St SE",
                                         "latitude": 38.88, "longitude": -76.93}}).json()
    assert d["location"]["name"] == "Benning Rd SE  -  A St SE" and d["location"]["latitude"] == 38.88
    assert d["location"]["sitenumbers"][0]["qch_lookup"] == "manual"
    assert "not_in_qch" not in [w["code"] for w in d["warnings"]]
    d = client.patch(url, json={"corners_linked": False}).json()
    assert d["location"]["corners_linked"] is False


# ------------------------------------------------------------------ group and split

def test_group_and_ungroup(loaded):
    client, ids = loaded
    a, b = ids["17408304"], ids["17889013"]
    client.patch(f"/speed/locations/{a}/directions/NB", json={"posted": 25})
    out = client.post("/speed/locations/group", json={"ids": [b, a]}).json()
    (group,) = out["location_ids"]
    assert [r["code"] for r in out["tab"]["rows"]] == ["04+13"] and out["tab"]["rows"][0]["is_grouped"]
    d = client.get(f"/speed/locations/{group}").json()
    assert d["directions"]["NB"]["posted"] == 25                                   # the first location's decisions
    assert [c["label"] for c in d["cams"]] == ["Cam 1 · 04", "Cam 2 · 04", "Cam 1 · 13"]
    assert d["paper_trail"][0]["what"] == "Grouped 17408304 + 17889013 into one report location"
    d = client.patch(f"/speed/locations/{group}/cams/17408304:1", json={"corner": "NE"}).json()
    assert [c["corner"] for c in d["cams"]] == ["NE", None, "NE"]                  # linked: cam 1 on both sitenumbers
    assert d["paper_trail"][0]["what"] == "Cam 1 corner set to NE (all sitenumbers)"
    out = client.post(f"/speed/locations/{group}/ungroup").json()
    assert len(out["location_ids"]) == 2 and [r["code"] for r in out["tab"]["rows"]] == ["04", "13"]
    assert trail(client, out["location_ids"][0])[0] == "Ungrouped from 04+13"
    assert "not a group" in refused(client.post(f"/speed/locations/{out['location_ids'][0]}/ungroup"))
    assert "two or more" in refused(client.post("/speed/locations/group", json={"ids": [out["location_ids"][0]]}))


def test_split_by_day_copies_decisions_and_unsplit_merges_back(loaded):
    client, ids = loaded
    loc = ids["17889013"]
    client.patch(f"/speed/locations/{loc}/directions/EB", json={"posted": 45, "source_cam": "1"})
    out = client.post(f"/speed/locations/{loc}/split-by-day").json()
    day1, day2 = out["location_ids"]
    assert [r["code"] for r in out["tab"]["rows"]] == ["04", "13 10-06", "13 10-07"]
    refused(client.get(f"/speed/locations/{loc}"), 404)                            # the parent is gone
    d1, d2 = (client.get(f"/speed/locations/{i}").json() for i in (day1, day2))
    assert (d1["directions"]["EB"]["report"]["n"], d2["directions"]["EB"]["report"]["n"]) == (7, 3)
    assert d2["directions"]["EB"]["posted"] == 45 and d2["directions"]["EB"]["source_cam"] == "17889013:1"
    assert d2["paper_trail"][0]["what"] == "Split 17889013 by day → Wed 10-07"
    assert d2["location"]["window"] == {"start_date": "2026-10-07", "start_time": "12:00 AM",
                                        "duration_hours": 24, "source": "slice"}
    assert "already one day" in refused(client.post(f"/speed/locations/{day1}/split-by-day"))
    out = client.post(f"/speed/locations/{day2}/unsplit").json()
    (whole,) = out["location_ids"]
    d = client.get(f"/speed/locations/{whole}").json()
    assert d["directions"]["EB"]["report"]["n"] == 10 and d["location"]["window"]["duration_hours"] == 48
    assert d["paper_trail"][0]["what"] == "Unsplit 17889013 (2 locations merged back)"
    assert "not split" in refused(client.post(f"/speed/locations/{whole}/unsplit"))
    assert "Only one count day" in refused(client.post(f"/speed/locations/{ids['17408304']}/split-by-day"))


def test_split_by_time_with_and_without_the_remainder(loaded):
    client, ids = loaded
    loc = ids["17889013"]
    out = client.post(f"/speed/locations/{loc}/split-by-time",
                      json={"windows": [{"from": 6, "to": 10}], "keep_remainder": True}).json()
    codes = [r["code"] for r in out["tab"]["rows"]]
    assert codes == ["04", "13 06–10", "13 10–17"]                                 # data runs 06:00 to 17:00
    am, rest = out["location_ids"]
    assert client.get(f"/speed/locations/{am}").json()["location"]["events"] == 8
    assert trail(client, rest)[0] == "Remainder of time split: 10:00–17:00"
    assert "already a time window" in refused(client.post(f"/speed/locations/{am}/split-by-time",
                                                          json={"windows": [{"from": 6, "to": 8}]}))
    whole = client.post(f"/speed/locations/{am}/unsplit").json()["location_ids"][0]
    out = client.post(f"/speed/locations/{whole}/split-by-time",
                      json={"windows": [{"from": 15, "to": 19}, {"from": 6, "to": 10}], "keep_remainder": False}).json()
    assert [r["code"] for r in out["tab"]["rows"]] == ["04", "13 06–10", "13 15–19"]
    whole = client.post(f"/speed/locations/{out['location_ids'][0]}/unsplit").json()["location_ids"][0]
    url = f"/speed/locations/{whole}/split-by-time"
    assert "overlap" in refused(client.post(url, json={"windows": [{"from": 6, "to": 10}, {"from": 9, "to": 12}]}))
    assert "after its start" in refused(client.post(url, json={"windows": [{"from": 10, "to": 6}]}))
    assert "at least one window" in refused(client.post(url, json={"windows": []}))


# ------------------------------------------------------------------ exports

def test_bin_export_is_every_vehicle_of_the_bin(loaded):
    client, ids = loaded
    r = client.get("/speed/export/17889013/1/EB", params={"bin": "40-49"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="17889013_cam-1_EB_40-49mph.csv"'
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["Date", "Time", "Direction", "Class", "Speed (In MPH)", "Track ID", "Sitenumber", "Cam"]
    assert [(x[0], x[1], x[2], x[4], x[6], x[7]) for x in rows[1:]] == [
        ("2026-10-06", "06:00:00 AM", "EB Thru", "40", "17889013", "1"),
        ("2026-10-06", "06:00:01 AM", "EB Thru", "41", "17889013", "1"),
        ("2026-10-06", "06:00:02 AM", "EB Thru", "42", "17889013", "1"),
        ("2026-10-06", "04:00:00 PM", "EB Thru", "44", "17889013", "1"),
        ("2026-10-06", "04:00:01 PM", "EB Thru", "45", "17889013", "1")]
    drawer = client.get(f"/speed/locations/{ids['17889013']}").json()
    bins = {b["from"]: b["n"] for b in drawer["histograms"]["17889013:1"]["EB"]["bins10"]}
    assert bins[40] == len(rows) - 1                                             # the count the table shows
    above = client.get("/speed/export/17889013/1/EB", params={"above": 51})
    assert above.headers["content-disposition"].endswith('EB_above-51mph.csv"')
    assert [x[4] for x in list(csv.reader(io.StringIO(above.text)))[1:]] == ["55", "61", "52"]
    # inside a location the export follows its slices and the cam's time mask, like the bin counts do
    day2 = client.post(f"/speed/locations/{ids['17889013']}/split-by-day").json()["location_ids"][1]
    sliced = client.get("/speed/export/17889013/1/EB", params={"bin": "50-59", "location": day2})
    assert [x[4] for x in list(csv.reader(io.StringIO(sliced.text)))[1:]] == ["50", "51", "52"]
    client.patch(f"/speed/locations/{day2}/cams/1", json={"mask": {"hours": ["2026-10-07 06"], "masked": True}})
    assert len(client.get("/speed/export/17889013/1/EB", params={"bin": "50-59"}).text.splitlines()) == 2
    assert "one of the two" in refused(client.get("/speed/export/17889013/1/EB"))
    assert "one of the two" in refused(client.get("/speed/export/17889013/1/EB", params={"bin": "50-59", "above": 60}))
    refused(client.get("/speed/export/17889013/9/EB", params={"bin": "50-59"}), 404)


# ------------------------------------------------------------------ the mockup's numbers, through the API

def test_the_api_reproduces_the_mockup_location_05(fixtures, samples, tmp_path):
    """174083 location 05 (five cams, one dead, WB excluded): imported and set up through the
    routes alone, the drawer the API returns equals the mockup's in every computed block."""
    want = fixtures["locations"]["4"]
    folder = tmp_path / "csv"
    folder.mkdir()
    for path in (samples / "raw datalens speed output").glob("17408305_cam-*_speed.csv"):
        (folder / path.name).write_bytes(path.read_bytes())
    project_folder = tmp_path / "p"
    project_folder.mkdir()
    client = TestClient(server.create_app(project.open_folder(project_folder, "174083")))
    report = client.post("/speed/import", json={"folder": str(folder), "use_qch": False}).json()
    loc = report["sites"][0]["location_id"]
    url = f"/speed/locations/{loc}"
    client.patch(url, json={"lane_config": {d: b["lane_config"] for d, b in want["directions"].items()}})
    for cam in want["cams"]:
        if cam["corner"]:
            client.patch(f"{url}/cams/{cam['key']}", json={"corner": cam["corner"]})
    for d, block in want["directions"].items():
        body = {"include_in_report": block["include_in_report"]}
        if block["source_cam"]:
            body["source_cam"] = block["source_cam"]
        client.patch(f"{url}/directions/{d}", json=body)
    client.patch(f"{url}/directions/NB", json={"posted": 25})
    client.patch(f"{url}/directions/EB", json={"posted": 25})
    got = client.get(url).json()
    assert contract.check_drawer(got) == []
    for block in ("histograms", "hourly", "bins", "posted_same", "status"):
        assert differences(got[block], want[block], block) == []
    for d in contract.DIRECTIONS:
        g, w = dict(got["directions"][d]), dict(want["directions"][d])
        g.pop("lane_config_qch"), w.pop("lane_config_qch")          # no QC Hub here: the snapshot is the assumed one
        if d == "WB":                       # E/W is "same both ways": the API wrote EB's 25 to WB (excluded) too;
            assert (g.pop("posted"), w.pop("posted")) == (25, None)       # the mockup's seed left it blank
        assert differences(g, w, d) == []
    assert differences(got["cams"], want["cams"], "cams") == []
    row = next(r for r in client.get("/speed/locations").json()["rows"] if r["id"] == loc)
    want_row = next(r for r in fixtures["tab"]["rows"] if r["id"] == 4)
    for field in ("code", "cams", "in_report", "events", "corners", "posted", "source_cams", "status"):
        assert row[field] == want_row[field], field


# ------------------------------------------------------------------ the development server's extras

def test_live_fixtures_and_the_mockup_route(loaded, proj, tmp_path):
    client, _ = loaded
    data = client.get("/speed/fixtures").json()
    assert contract.check_fixtures(data) == [] and len(data["locations"]) == 2
    mock = tmp_path / "ui mockup"
    mock.mkdir()
    (mock / server.VIEWER_PAGE).write_text("<html>viewer</html>", encoding="utf-8")
    viewer = TestClient(server.create_app(proj, str(mock)))
    assert viewer.get("/mockup/").text == "<html>viewer</html>"
    js = viewer.get("/mockup/speed_fixtures.js").text
    assert js.startswith("window.SPEED_FIXTURES={") and '"contract_version":1' in js
    empty = TestClient(server.create_app(project.open_folder(tmp_path, "111111"), str(tmp_path)))
    assert empty.get("/speed/fixtures").status_code == 404
    assert empty.get("/mockup/speed_fixtures.js").text == "window.SPEED_FIXTURES=null;\n"
    assert empty.get("/mockup/").status_code == 404
