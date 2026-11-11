"""SP-0: the fixture extracted from the locked mockup is the contract, and it holds together."""

from __future__ import annotations

import copy

from art_speed import contract


def test_fixtures_match_the_contract(fixtures):
    assert contract.check_fixtures(fixtures) == []


def test_fixtures_are_the_mockup_data(fixtures):
    """174083's five locations plus the weeklong 17889013 export as location 13."""
    rows = fixtures["tab"]["rows"]
    assert [r["code"] for r in rows] == ["01", "02", "04", "05", "06", "13"]
    assert [r["sitenumbers"] for r in rows][-1] == ["17889013"]
    assert fixtures["tab"]["summary"] == {
        "sitenumbers": 6, "locations": 6, "cams": 16, "last_import": "2026-10-12 09:37",
        "qch_snapshot": "2026-10-12 09:37", "last_report": None}
    weeklong = fixtures["locations"]["6"]
    assert weeklong["cams"][0]["events"] == 269187
    assert weeklong["cams"][0]["csv_form"] == "post-macro"
    assert weeklong["cams"][0]["masked_hours"] == ["2026-10-07 10", "2026-10-07 11"]
    assert weeklong["location"]["window"]["duration_hours"] == 168
    assert len(weeklong["location"]["count_days"]) == 7


def test_every_drawer_has_four_directions_and_24_hour_rows(fixtures):
    for payload in fixtures["locations"].values():
        assert tuple(payload["directions"]) == contract.DIRECTIONS
        for d, block in payload["hourly"]["directions"].items():
            assert payload["directions"][d]["include_in_report"]
            if block:
                assert all(len(day["cells"]) == 24 for day in block["days"])
                assert [day["date"] for day in block["days"]] == payload["hourly"]["dates"]


def test_report_values_only_for_included_directions_with_a_source(fixtures):
    for payload in fixtures["locations"].values():
        for d, block in payload["directions"].items():
            has = block["report"] is not None
            assert has == (block["include_in_report"] and block["source_cam"] is not None)
            if has:
                assert block["report"]["source_cam"] == block["source_cam"]
                assert block["source_cam"] in payload["histograms"]


def test_tab_row_agrees_with_its_drawer(fixtures):
    for row in fixtures["tab"]["rows"]:
        payload = fixtures["locations"][str(row["id"])]
        assert row["status"] == payload["status"]
        assert row["warnings"] == [w["text"] for w in payload["warnings"]]
        assert row["events"] == payload["location"]["events"]
        assert row["in_report"] == [d for d in contract.DIRECTIONS
                                    if payload["directions"][d]["include_in_report"]]


def test_generate_follows_the_green_rule(fixtures):
    states = [r["status"]["state"] for r in fixtures["tab"]["rows"]]
    gen = fixtures["tab"]["generate"]
    assert gen["enabled"] == (contract.READY in states)
    assert gen["green"] == (all(s == contract.READY for s in states) and bool(states))


def test_the_checker_catches_a_missing_and_an_extra_field(fixtures):
    payload = copy.deepcopy(fixtures["locations"]["1"])
    del payload["directions"]["NB"]["posted"]
    payload["location"]["surprise"] = 1
    payload["cams"][0]["corner"] = "north"
    errors = contract.check_drawer(payload)
    assert any("directions.NB.posted: missing" in e for e in errors)
    assert any("location.surprise: not in the contract" in e for e in errors)
    assert any("cams[0].corner" in e for e in errors)
