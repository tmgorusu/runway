import json

import pandas as pd
import pytest

from runway import austin, value
from runway.paths import ROOT


@pytest.fixture(scope="module")
def v():
    return value.build()


def test_value_uses_only_the_brief_dollar_figure(v):
    assert v["usd_per_kw_interval"] == 17.0 and v["contract_mw"] == 40.0
    assert v["value_at_stake_usd"] == 17.0 * 40_000 * 4
    for r in v["thresholds"]:
        assert r["value_caught_usd"] == 17.0 * 40_000 * r["intervals_caught"]


def test_committed_row_matches_the_calendar_and_hero(v):
    flags = json.loads((ROOT / "handoff" / "hit_flags.json").read_text())
    c = v["committed"]
    assert c["threshold"] == 0.97 and c["calls"] == flags["n_calls"] and c["intervals_caught"] == flags["n_hits"]
    hero = json.loads((ROOT / "outputs" / "track1" / "hero.json").read_text())["assumption_sets"]["nominal"]
    assert c["wear_share_on_misses"] == pytest.approx(hero["wear_fraction_on_misses"], rel=1e-9)
    assert [r["threshold"] for r in v["thresholds"]] == [0.96, 0.97, 0.98]
    calls = [r["calls"] for r in v["thresholds"]]
    assert calls == sorted(calls, reverse=True)


def test_plan_export_respects_every_limit(tmp_path):
    out = austin.plan("2025-07-30", path=tmp_path / "plan.csv")
    df = pd.read_csv(out["path"])
    assert len(df) == 4000 and set(df.columns) >= {"unit_id", "zone", "power_kw", "feasible_kw", "stale_telemetry"}
    assert (df["power_kw"] <= df["feasible_kw"] + 1e-6).all() and (df["power_kw"] >= 0).all()
    assert abs(df["power_kw"].sum() - out["requested_kw"]) <= 1.0
    assert set(df.loc[df["power_kw"] > 0, "zone"]) <= set(out["result"]["members"])


def test_plan_honors_zones_taken_out(tmp_path):
    first = austin.plan("2025-07-30", path=tmp_path / "a.csv")["result"]["members"][:2]
    df = pd.read_csv(austin.plan("2025-07-30", out_zones=first, path=tmp_path / "b.csv")["path"])
    assert df.loc[df["zone"].isin(first), "power_kw"].sum() == 0.0


def test_game_bench_schema(tmp_path):
    out = austin.bench_game(sizes=(500, 1000), path=tmp_path / "game.json")
    assert [r["n_units"] for r in out["results"]] == [500, 1000]
    assert all(r["game_ms"] > 0 and r["total_ms"] >= r["game_ms"] for r in out["results"])
    assert all(abs(r["delivered_kw"] - r["mw_requested"] * 1000) <= 1.0 for r in out["results"])
