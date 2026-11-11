"""SP-3: the engine's definitions, one at a time (plan section 6, confirmed SP-Q12)."""

from __future__ import annotations

import pytest

from art_speed import engine


def hist(values) -> dict:
    out: dict = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def test_percentile_inc_is_excels():
    # =PERCENTILE.INC({1,2,3,4},0.85) = 3.55 ; {10,20,30,40,50} at .85 = 44 ; at .95 = 48
    assert engine.percentile_inc(hist([1, 2, 3, 4]), 0.85) == pytest.approx(3.55)
    assert engine.percentile_inc(hist([10, 20, 30, 40, 50]), 0.85) == pytest.approx(44)
    assert engine.percentile_inc(hist([10, 20, 30, 40, 50]), 0.95) == pytest.approx(48)
    assert engine.percentile_inc(hist([7]), 0.85) == 7
    assert engine.percentile_inc({}, 0.85) is None
    # over bin counts it equals the percentile over the expanded rows
    values = [12] * 40 + [25] * 500 + [26] * 300 + [31] * 99 + [55]
    assert engine.percentile_inc(hist(values)) == sorted(values)[int((len(values) - 1) * 0.85)]


def test_whole_mph_rounds_half_up_never_bankers():
    assert [engine.js_round(x) for x in (26.5, 27.5, 13.95, 26.49, 0.5)] == [27, 28, 14, 26, 1]


def test_bin_rows_and_hour_keys():
    rows = [("2026-02-24", "06:00:12", "NB", 39), ("2026-02-24", "06:59:59", "NB", 39),
            ("2026-02-24", "07:00:00", "NB", 0), ("2026-02-25", "06:00:00", "SB", 20)]
    assert engine.bin_rows(rows) == {"NB": {"2026-02-24 06": {39: 2}, "2026-02-24 07": {0: 1}},
                                     "SB": {"2026-02-25 06": {20: 1}}}


def test_slices_filter_by_date_and_by_hour_window():
    assert engine.slice_has({}, "2026-10-07 10")
    assert engine.slice_has({"date": "2026-10-07"}, "2026-10-07 10")
    assert not engine.slice_has({"date": "2026-10-06"}, "2026-10-07 10")
    am = {"from_hour": 6, "to_hour": 10}
    assert [engine.slice_has(am, f"2026-10-07 {h:02d}") for h in (5, 6, 9, 10)] == [False, True, True, False]
    assert engine.slice_has({"date": "2026-10-07", **am}, "2026-10-07 06")
    assert not engine.slice_has({"date": "2026-10-07", **am}, "2026-10-08 06")
    assert not engine.in_slices([], "2026-10-07 10")            # no slice on this sitenumber: nothing


BINS = {"2026-02-24 06": {0: 3, 4: 2, 5: 1, 20: 4}, "2026-02-24 07": {0: 1, 30: 2, 70: 1},
        "2026-02-25 06": {25: 5}}


def test_order_slice_then_time_mask_then_zeros():
    everything = engine.observe(BINS, [{}])
    assert (everything.n, everything.zero, everything.n_nonzero, everything.masked_hours) == (19, 4, 15, 0)
    assert everything.hist == {4: 2, 5: 1, 20: 4, 30: 2, 70: 1, 25: 5}
    one_day = engine.observe(BINS, [{"date": "2026-02-24"}])
    assert (one_day.n, one_day.zero) == (14, 4)
    masked = engine.observe(BINS, [{"date": "2026-02-24"}], masked=["2026-02-24 07", "2026-02-25 06"])
    assert (masked.n, masked.zero, masked.masked_hours) == (10, 3, 1)     # the masked hour outside the slice is not counted
    assert masked.hist == {4: 2, 5: 1, 20: 4}


def test_raw_stats_are_before_any_speed_mask():
    st = engine.raw_stats(engine.observe(BINS, [{}]))
    assert (st["n"], st["zero"], st["n_nonzero"]) == (19, 4, 15)
    assert st["pct_zero"] == pytest.approx(4 / 19)
    assert st["avg"] == pytest.approx((8 + 5 + 80 + 60 + 70 + 125) / 15)
    empty = engine.raw_stats(engine.observe({}, [{}]))
    assert (empty["n"], empty["pct_zero"], empty["avg"], empty["p85"]) == (0, 0, None, None)


def test_lower_mask_5_means_below_5_is_masked():
    obs = engine.observe(BINS, [{}])
    f = engine.final_stats(obs, low=5, high=None)
    assert (f["events"], f["zero"], f["masked"], f["n"]) == (19, 4, 2, 13)       # the two 4s go, the 5 stays
    assert f["pct_masked"] == pytest.approx(2 / 15)
    assert engine.final_stats(obs, low=None, high=None)["n"] == 15


def test_upper_mask_then_adjust_then_stats():
    obs = engine.observe(BINS, [{}])
    f = engine.final_stats(obs, low=5, high=60, adjust_by=-2, posted=25)
    assert (f["masked"], f["n"]) == (3, 12)                                     # 4, 4 and 70
    # kept, adjusted: 3, 18 x4, 28 x2, 23 x5 - the mask is on the raw speed, the adjustment after
    assert f["avg"] == pytest.approx((3 + 72 + 56 + 115) / 12)
    assert f["over_posted"] == 2 and f["pct_over_posted"] == pytest.approx(2 / 12)
    up = engine.final_stats(obs, low=5, high=60, adjust_by=+3, posted=25)
    assert up["over_posted"] == 7                                               # 28 x5 and 33 x2 are over 25


def test_over_posted_is_strictly_greater_and_needs_a_posted_speed():
    obs = engine.observe({"2026-02-24 06": {25: 10, 26: 1}}, [{}])
    assert engine.final_stats(obs, low=5, high=None, posted=25)["over_posted"] == 1
    none = engine.final_stats(obs, low=5, high=None, posted=None)
    assert none["over_posted"] is None and none["pct_over_posted"] is None


def test_everything_masked_gives_no_numbers_not_zeros():
    obs = engine.observe({"2026-02-24 06": {3: 2, 0: 1}}, [{}])
    f = engine.final_stats(obs, low=5, high=None, posted=25)
    assert (f["n"], f["avg"], f["p85"], f["p95"], f["pct_over_posted"]) == (0, None, None, None, None)


def test_hour_stats_keep_the_struck_value_of_a_masked_hour():
    st = engine.hour_stats({0: 1, 4: 1, 30: 2, 70: 1}, low=5, high=60, adjust_by=1)
    assert (st["rows"], st["zero"], st["n"]) == (5, 1, 2) and st["p85"] == 31 and st["kept"] == {31: 2}


def test_bins10_run_from_lowest_to_highest_and_flag_bins_above_the_upper_mask():
    out = engine.bins10({12: 3, 18: 1, 41: 2, 63: 1}, upper_mask=45)
    assert [(b["from"], b["to"], b["n"], b["above_upper_mask"]) for b in out] == [
        (10, 19, 4, False), (20, 29, 0, False), (30, 39, 0, False), (40, 49, 2, False),
        (50, 59, 0, True), (60, 69, 1, True)]
    assert engine.bins10({}) == []


def test_roles_from_corners():
    assert [engine.role("NE", d) for d in engine.DIRECTIONS] == ["Primary", "", "", "Secondary"]
    assert [engine.role("SW", d) for d in engine.DIRECTIONS] == ["", "Primary", "Secondary", ""]
    assert engine.role(None, "NB") == ""


def cand(cam, role, pz, rows=100, usable=True):
    return {"cam": cam, "role": role, "pct_zero": pz, "rows": rows, "usable": usable}


def test_suggestion_primary_then_secondary_then_lowest_zero():
    assert engine.suggest([cand("a", "Secondary", 0.0), cand("b", "Primary", 0.4)]) == {
        "cam": "b", "why": "primary corner"}
    assert engine.suggest([cand("a", "Secondary", 0.2), cand("b", "", 0.0)]) == {
        "cam": "a", "why": "no primary · secondary corner"}
    assert engine.suggest([cand("a", "", 0.2), cand("b", "", 0.1)]) == {
        "cam": "b", "why": "no on-axis cam · lowest % zero"}


def test_suggestion_ties_and_dead_cams():
    two = engine.suggest([cand("a", "Primary", 0.3), cand("b", "Primary", 0.1)])
    assert two == {"cam": "b", "why": "primary corner · lowest % zero of 2"}
    same = engine.suggest([cand("a", "Primary", 0.1), cand("b", "Primary", 0.1)])
    assert same["cam"] == "a"                                   # a full tie goes to the lower cam
    dead = engine.suggest([cand("a", "Primary", 1.0, usable=False), cand("b", "Secondary", 0.1)])
    assert dead == {"cam": "b", "why": "no primary · secondary corner · dead cam skipped"}
    assert engine.suggest([cand("a", "Primary", 1.0, usable=False)]) is None
    assert engine.suggest([cand("a", "Primary", 0.0, rows=0)]) is None      # no rows in this direction
