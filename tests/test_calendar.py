import json

import pandas as pd
import pytest

from runway import calendar as cal
from runway import ingest

SPEC_RULE = """# Calling rule (test copy of VISION section 4)
A calendar day in America/Chicago, in June, July, August, or September 2025, is a candidate
when that day's maximum day-ahead ERCOT system forecast is at least 0.97 times the maximum
daily day-ahead peak from the first of that month through that day, inclusive.
"""


@pytest.fixture
def rule(tmp_path):
    """The real CALLING_RULE.md once Wear commits it, else the VISION wording."""
    if cal.RULE.exists():
        return cal.RULE
    p = tmp_path / "CALLING_RULE.md"
    p.write_text(SPEC_RULE)
    return p


def test_threshold_is_097(rule):
    assert cal.rule_threshold(rule) == 0.97


def test_threshold_parser_forms(tmp_path):
    p = tmp_path / "r.md"
    p.write_text("threshold: 0.97\n")
    assert cal.rule_threshold(p) == 0.97
    p.write_text("is at least **0.97** times the max")
    assert cal.rule_threshold(p) == 0.97


def test_every_published_4cp_hit_or_reasoned(rule):
    calls, flags = cal.run(rule_path=rule, write=False)
    assert len(flags) == 4
    for f in flags:
        assert f["hit"] or f["miss_reason"], f
    assert set(calls.columns) == set(cal.COLUMNS)
    assert (calls["rule_hash"] == cal.rule_hash(rule)).all()
    assert ((calls["peak_odds"] >= 0.97) & (calls["peak_odds"] <= 1.0)).all()
    assert (calls["duration_min"] == 90).all() and (calls["utility"] == "Austin Energy").all()
    assert (calls["source"] == "4cp_candidate").all()
    assert (~calls["hit"] == (calls["miss_reason"] != "")).all()
    local = calls["start"].dt.tz_convert(cal.TZ)
    assert set(local.dt.month) <= {6, 7, 8, 9}
    assert calls["call_id"].is_unique  # one call per candidate day


def test_first_of_month_is_always_candidate(rule):
    calls, _ = cal.run(rule_path=rule, write=False)
    days = set(calls["call_id"])
    for m in ("06", "07", "08", "09"):
        assert f"4cp-2025-{m}-01" in days


def test_call_window_brackets_forecast_peak(rule):
    calls, _ = cal.run(rule_path=rule, write=False)
    peaks = cal.daily_peaks(ingest.load("ercot_load")).set_index("day")
    for _, c in calls.iterrows():
        day = pd.Timestamp(c["call_id"][4:]).date()
        assert c["start"] == peaks.loc[day, "peak_utc"] - pd.Timedelta(minutes=45)


def test_hand_built_window():
    tz = cal.TZ
    hours = pd.date_range("2025-06-01 01:00", "2025-06-03 00:00", freq="h", tz=tz).tz_convert("UTC")
    fc = [1000.0] * len(hours)
    fc[16] = 2000.0   # June 1, hour ending 17:00 local
    fc[24 + 16] = 1950.0  # June 2, same hour, ratio 0.975
    load = pd.DataFrame({"ts_utc": hours, "load_mw": fc, "forecast_mw": fc})
    end = pd.Timestamp("2025-06-02 17:00", tz=tz).tz_convert("UTC")
    cp = pd.DataFrame({"month": ["June"], "interval_end_local": ["6/02/2025 17:00"],
                       "interval_end_utc": [end], "interval_start_utc": [end - pd.Timedelta(minutes=15)]})
    weather = pd.DataFrame({"ts_utc": hours, "city": "Austin", "temp_c": 36.0})
    calls, flags = cal.build_calls(load, cp, weather, 0.97, "h")
    assert list(calls["call_id"]) == ["4cp-2025-06-01", "4cp-2025-06-02"]
    june2 = calls.iloc[1]
    assert june2["start"] == pd.Timestamp("2025-06-02 15:45", tz=tz)
    assert june2["hit"] and calls.iloc[0]["miss_reason"] == "no_4cp_on_day"
    assert june2["peak_odds"] == pytest.approx(0.975)
    assert flags[0]["hit"] and flags[0]["call_id"] == "4cp-2025-06-02"
    calls98, flags98 = cal.build_calls(load, cp, weather, 0.98, "h")
    assert list(calls98["call_id"]) == ["4cp-2025-06-01"] and flags98[0]["miss_reason"] == "day_not_candidate"


def test_frozen_rule_after_hit_flags():
    """CI guard: once hit_flags.json exists, the rule file and its 0.97 cannot change."""
    if not cal.HIT_FLAGS.exists():
        pytest.skip("hit_flags.json not written yet")
    flags = json.loads(cal.HIT_FLAGS.read_text())
    assert flags["threshold"] == 0.97
    assert cal.rule_threshold() == 0.97
    assert flags["rule_hash"] == cal.rule_hash(), "CALLING_RULE.md changed after hit_flags.json exists"
    calls = pd.read_parquet(cal.CALLS)
    assert (calls["rule_hash"] == flags["rule_hash"]).all()
