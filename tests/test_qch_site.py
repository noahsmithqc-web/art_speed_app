"""SP-1: what QC Hub knows about a speed sitenumber (the lookup's own SQL, against a fake)."""

from __future__ import annotations

from art_speed.qch import connection, credentials, site
from tests.fakes import qch as fakes


def test_turn_movement_site_gives_names_lanes_location_and_window():
    qch = fakes.FakeQch(fakes.tm_site(17408304))
    found = site.lookup_site(qch, 17408304)
    assert found.lookup == site.LEGACYCOUNTS and found.found and found.checked
    assert (found.street_ns, found.street_ew) == ("Benning Rd SE", "A St SE")       # trimmed
    assert found.name == "Benning Rd SE  -  A St SE"
    assert found.lane_configs == {"NB": "6200000", "SB": "2300000", "EB": "0000000", "WB": "7000000"}
    assert (found.control_type, found.stop_controlled) == ("TWSC", "EW")
    assert (found.latitude, found.longitude) == (38.8884457, -76.9369868)
    assert (found.count_date, found.start_time, found.duration_hr) == ("2026-02-24", "06:00:00", 13.0)


def test_a_24_hour_count_has_end_equal_to_start():
    qch = fakes.FakeQch(fakes.tm_site(17650202, dates=("2026-06-03",), start="00:00:00", end="00:00:00"))
    found = site.lookup_site(qch, 17650202)
    assert (found.start_time, found.duration_hr) == ("00:00:00", 24.0)


def test_several_scheduled_dates_make_one_window_across_them():
    days = tuple(f"2026-10-{d:02d}" for d in range(6, 13))
    qch = fakes.FakeQch(fakes.tm_site(17889013, dates=days, start="00:00:00", end="00:00:00"))
    found = site.lookup_site(qch, 17889013)
    assert (found.count_date, found.duration_hr) == ("2026-10-06", 168.0)
    assert any("7 scheduled dates" in n for n in found.notes)


def test_tube_site_has_one_name_and_no_lanes():
    qch = fakes.FakeQch(fakes.tube_site(17777505))
    found = site.lookup_site(qch, 17777505)
    assert found.lookup == site.ORDERLOCATION
    assert found.name == "SR 85 N of Camp St" and not found.has_lane_configs
    assert (found.city, found.state, found.latitude) == ("Fayetteville", "GA", 33.4488)
    assert found.count_date is None and found.duration_hr is None


def test_tube_site_without_a_main_street_joins_the_names():
    qch = fakes.FakeQch(fakes.tube_site(17777506, streets=("Camp St", "SR 85"), codes=()))
    found = site.lookup_site(qch, 17777506)
    assert found.name == "Camp St / SR 85"
    assert any("none marked MAIN" in n for n in found.notes)


def test_unknown_sitenumber_is_not_found():
    found = site.lookup_site(fakes.FakeQch(), 17999901)
    assert found.lookup == site.NOT_FOUND and not found.found and found.checked and found.name == ""


def test_a_bad_lane_config_is_dropped_not_stored():
    qch = fakes.FakeQch(fakes.tm_site(17408304, NBLaneConfig="62x", SBLaneConfig=None))
    found = site.lookup_site(qch, 17408304)
    assert found.lane_configs["NB"] == "" and found.lane_configs["SB"] == ""


def test_qch_down_leaves_every_site_unchecked_and_never_raises():
    qch = fakes.FakeQch(fakes.tm_site(17408304), fail_on="legacycounts")
    found = site.lookup_sites([17408304, 17408305], qch)
    assert [found[n].lookup for n in (17408304, 17408305)] == [None, None]
    assert not found[17408304].checked and "not checked" in found[17408304].describe()


def test_no_login_is_not_an_error():
    found = site.lookup_sites([17408304])            # opens its own connection: there is no login
    assert found[17408304].lookup is None and "Settings" in found[17408304].message


def test_street_query_refused_costs_only_the_name():
    qch = fakes.FakeQch(fakes.tube_site(17777505), fail_on="orderlocationstreet",
                        fail_reason=connection.QUERY_FAILED)
    found = site.lookup_site(qch, 17777505)
    assert found.lookup == site.ORDERLOCATION and found.name == ""


def test_dev_credentials_file(monkeypatch, tmp_path):
    f = tmp_path / "qch.creds"
    f.write_text("host=h\nuser=u\npassword=p\ndatabase=d\n")
    monkeypatch.setenv("SPEED_QCH_CREDENTIALS", str(f))
    assert credentials.load().safe == "u@h:3306/d"
    f.write_text("host=h\n")
    try:
        credentials.load()
    except credentials.CredentialsMissing as exc:
        assert "user, password, database" in str(exc)
    else:
        raise AssertionError("expected CredentialsMissing")
