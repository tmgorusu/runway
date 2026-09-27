from dataclasses import replace

import pytest

from runway import replay
from runway.study import allocate_arrays
from runway.fleet import make_fleet
from runway.hero import fixture_calls as _pairs
from runway.paths import sha256_file
from runway.marginal import unit_arrays


def fixture_calls():
    return [c for c, _ in _pairs()]


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    d = tmp_path_factory.mktemp("replay")
    s = replay.run(fixture_calls(), n=400, over_call=False, out_path=d / "s.json", detail_path=d / "d.json")
    return s, d


def test_reserve_and_feasible_gate(small):
    s, _ = small
    assert all(v == 0 for v in s["reserve_violations"].values())
    units = make_fleet(400)
    arr = unit_arrays(units)
    arr["soc"][:] = 1.0
    for c in fixture_calls():
        caps = replay._caps(arr, c.hours)
        for policy in ("runway", "even", "most_charge"):
            _, short = allocate_arrays(replace(c, mw_requested=s["feasible_mw"]), arr, caps, policy)
            assert short <= 1e-6
    assert s["feasible_mw"] % replay.MW_GRID_STEP == 0


def test_saturated_row_shortfall():
    row = replay.saturated_row(fixture_calls(), n=2000)
    assert all(v >= 3400.0 for v in row.values())
    assert max(row.values()) - min(row.values()) <= 1e-6


def test_byte_identical(tmp_path, small):
    _, d = small
    replay.run(fixture_calls(), n=400, over_call=False, out_path=tmp_path / "s.json", detail_path=tmp_path / "d.json")
    assert sha256_file(tmp_path / "s.json") == sha256_file(d / "s.json")


def test_summary_schema(small):
    s, _ = small
    assert list(s) == ["synthetic", "seed", "feasible_mw", "replacement_health", "reserve_frac", "reserve_violations",
                       "early_replacements", "saturated_2000_shortfall_kw", "over_call_all_policies_fail",
                       "ranking_flipped_across_assumption_sets", "repeated_2025_labeled"]
    assert s["replacement_health"] == 0.70 and s["reserve_frac"] == 0.30


def test_over_call_fixture_fails_all_policies():
    """VISION invariant 6: the saturated fleet (2,000 units asked for 40 MW), called daily at 44.0 C for 10
    repeated years, puts units below the replacement line under every policy."""
    results = replay.over_call_results()
    print({p: (r["early_replacements"], round(r["min_health"], 4)) for p, r in results.items()})
    assert all(r["reserve_violations"] == 0 for r in results.values())
    assert all(r["early_replacements"] >= 1 for r in results.values())
    assert all(r["shortfall_calls"] >= 1 for r in results.values())
