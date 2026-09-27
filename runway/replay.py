"""Season replay of summer 2025 on the synthetic fleet, three policies, three assumption sets.

Per day: calendar aging at the daily-mean enclosure temperature with SOC 1.0
(units sit at full charge for backup, an assumption). On a call day: allocate at
the feasible megawatt level, reject any reserve breach, add the call-attributable
wear from step_thermal and age, then the cycle wear of recharging at
recharge_kw. Units whose health falls below 0.70 count as early replacements.

The over-call fixture repeats one hot call per day for 10 years and is labeled
as a repeated year. It integrates in 10-day chunks and re-solves every 30 days.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from runway.aging import age_arrays, k_cal, power_increment
from runway.assumptions import SETS, load
from runway.contracts import CAPACITY_KWH, P_MAX_KW, REPLACEMENT_HEALTH, RESERVE_FRAC, Call
from runway.fleet import make_fleet
from runway.marginal import call_wear, unit_arrays
from runway.paths import OUTPUTS, write_json
from runway.study import REPLAY_START as FLEET_EPOCH
from runway.study import allocate_arrays, daily_mean_austin, dt_s

ENERGY_TOL_KWH = 1e-9

SUMMARY_PATH = OUTPUTS / "replay" / "summary.json"
DETAIL_PATH = OUTPUTS / "replay" / "detail.json"
POLICIES = ("runway", "even", "most_charge")
CHICAGO = ZoneInfo("America/Chicago")
MW_GRID_STEP = 0.5
OVER_CALL_AMBIENT_C = 44.0
OVER_CALL_IDLE_C = 34.0
OVER_CALL_YEARS = 10


@dataclass
class Step:
    when: datetime
    days: float
    idle_c: float
    call: Call | None = None
    n_calls: int = 0


def _caps(state: dict, hours: float) -> np.ndarray:
    energy = np.maximum(0.0, CAPACITY_KWH * state["health"] * (state["soc"] - RESERVE_FRAC))
    return np.minimum(P_MAX_KW, energy / hours)


def run_fleet(units, steps: list[Step], policy: str, assumption: str, mw: float, resolve_every: int = 1) -> dict:
    p = load(assumption)
    state = unit_arrays(units, assumption)
    state["last_seen_s"][:] = np.nan
    state["soc"] = np.ones(len(units))
    start_health = state["health"].copy()
    replaced = np.zeros(len(units), dtype=bool)
    violations = 0
    shortfall_calls = 0
    max_short = 0.0
    power = None
    solves = 0
    call_steps = 0
    for step in steps:
        dt_days = step.days
        idle_enclosure = step.idle_c + p["solar_gain_w_at_sun1"] * p["solar_daily_mean_fraction"] * state["sun"] / p["ua_w_per_k"]
        idle = k_cal(p, idle_enclosure + 273.15, 1.0) * power_increment(state["age_years"] * 365.25, dt_days, p["pcal"])
        wear = idle
        if step.call is not None:
            call = replace(step.call, mw_requested=mw)
            caps = _caps(state, call.hours)
            if power is None or call_steps % resolve_every == 0:
                power, short = allocate_arrays(call, state, caps, policy, assumption)
                solves += 1
                if short > 1e-6:
                    shortfall_calls += 1
                    max_short = max(max_short, short)
            call_steps += 1
            power = np.minimum(power, caps)
            energy = np.maximum(0.0, CAPACITY_KWH * state["health"] * (state["soc"] - RESERVE_FRAC))
            violations += int(np.sum(power * call.hours > energy + ENERGY_TOL_KWH))
            per_call = call_wear(state, call.ambient_c, power, dt_s(call), assumption, attributable=True)
            efc_step = power * call.hours / (2.0 * CAPACITY_KWH)
            recharge_s = np.where(power > 0, power * call.hours / p["recharge_kw"] * 3600.0, 0.0)
            rk = np.where(power > 0, p["recharge_kw"], 0.0)
            recharge = (age_arrays(p, idle_enclosure, rk, recharge_s, 1.0, state["age_years"], state["efc"] + efc_step)
                        - age_arrays(p, idle_enclosure, 0.0, recharge_s, 1.0, state["age_years"], state["efc"] + efc_step))
            wear = wear + step.n_calls * (per_call + recharge)
            state["efc"] = state["efc"] + step.n_calls * 2.0 * efc_step
        state["health"] = state["health"] - wear
        state["age_years"] = state["age_years"] + dt_days / 365.25
        replaced |= state["health"] < REPLACEMENT_HEALTH
    return {
        "total_capacity_fraction_lost": float(np.sum(start_health - state["health"])),
        "early_replacements": int(replaced.sum()),
        "reserve_violations": violations,
        "shortfall_calls": shortfall_calls,
        "max_shortfall_kw": max_short,
        "solves": solves,
        "min_health": float(state["health"].min()),
    }


def season_steps(calls: list[Call], daily: dict | None = None) -> list[Step]:
    by_day = {c.start.astimezone(CHICAGO).date(): c for c in calls}
    start = FLEET_EPOCH.astimezone(CHICAGO).date()
    end = max(max(by_day), datetime(2025, 9, 30).date()) if by_day else datetime(2025, 9, 30).date()
    steps = []
    day = start
    while day <= end:
        idle = daily.get(day, 30.0) if daily else (by_day[day].ambient_c - 8.0 if day in by_day else 30.0)
        c = by_day.get(day)
        steps.append(Step(datetime(day.year, day.month, day.day, 5, tzinfo=timezone.utc), 1.0, idle, c, 1 if c else 0))
        day += timedelta(days=1)
    return steps


def over_call_steps(chunk_days: int = 10, years: int = OVER_CALL_YEARS) -> list[Step]:
    call = Call("over-call-fixture", FLEET_EPOCH + timedelta(hours=17), 90.0, 40.0, "Austin Energy", 0.99, OVER_CALL_AMBIENT_C)
    n = int(round(years * 365 / chunk_days))
    return [Step(FLEET_EPOCH + timedelta(days=i * chunk_days), float(chunk_days), OVER_CALL_IDLE_C,
                 replace(call, start=FLEET_EPOCH + timedelta(days=i * chunk_days, hours=17)), chunk_days)
            for i in range(n)]


def feasible_mw(units, calls: list[Call], start_mw: float = 40.0) -> float:
    """Largest MW on a 0.5 MW grid at which all three policies have zero shortfall on every call."""
    state = unit_arrays(units)
    state["last_seen_s"][:] = np.nan
    state["soc"] = np.ones(len(units))
    mw = start_mw
    while mw > 0:
        ok = True
        for c in calls:
            caps = _caps(state, c.hours)
            for policy in ("even", "most_charge"):
                _, short = allocate_arrays(replace(c, mw_requested=mw), state, caps, policy)
                if short > 1e-6:
                    ok = False
            if not ok:
                break
        if ok:
            return mw
        mw -= MW_GRID_STEP
    return 0.0


def saturated_row(calls: list[Call], n: int = 2000, seed: int = 7, mw: float = 40.0) -> dict:
    units = make_fleet(n, seed)
    state = unit_arrays(units)
    state["last_seen_s"][:] = np.nan
    state["soc"] = np.ones(n)
    out = {}
    for policy in POLICIES:
        shorts = [allocate_arrays(replace(c, mw_requested=mw), state, _caps(state, c.hours), policy)[1] for c in calls]
        out[policy] = float(np.mean(shorts)) if shorts else 0.0
    return out


def run(calls: list[Call], n: int = 4000, seed: int = 7, sets=SETS, over_call: bool = True,
        daily: dict | None = None, out_path=SUMMARY_PATH, detail_path=DETAIL_PATH) -> dict:
    units = make_fleet(n, seed)
    mw = feasible_mw(units, calls, 40.0 * n / 4000)
    steps = season_steps(calls, daily)
    season = {s: {pol: run_fleet(units, steps, pol, s, mw) for pol in POLICIES} for s in sets}
    rankings = {s: sorted(POLICIES, key=lambda pol: (season[s][pol]["total_capacity_fraction_lost"], pol)) for s in sets}
    flipped = len({tuple(r) for r in rankings.values()}) > 1
    nominal = season["nominal"]
    over = {}
    if over_call:
        o_units = make_fleet(4000, seed)
        o_steps = over_call_steps()
        over = {pol: run_fleet(o_units, o_steps, pol, "nominal", mw * 4000 / n, resolve_every=3) for pol in POLICIES}
    summary = {
        "synthetic": True,
        "seed": seed,
        "feasible_mw": mw,
        "replacement_health": REPLACEMENT_HEALTH,
        "reserve_frac": RESERVE_FRAC,
        "reserve_violations": {pol: nominal[pol]["reserve_violations"] + (over[pol]["reserve_violations"] if over else 0)
                               for pol in POLICIES},
        "early_replacements": {pol: nominal[pol]["early_replacements"] for pol in POLICIES},
        "saturated_2000_shortfall_kw": saturated_row(calls, 2000 * n // 4000 or 1, seed, 40.0 * n / 4000),
        "over_call_all_policies_fail": bool(over) and all(over[pol]["early_replacements"] >= 1 for pol in POLICIES),
        "ranking_flipped_across_assumption_sets": flipped,
        "repeated_2025_labeled": True,
    }
    detail = {
        "synthetic_fleet": True,
        "data_source": "ERCOT 2025 (EIA-930 load and forecast, NP9-83-M 4CP), Open-Meteo weather",
        "fleet_n": n,
        "feasible_mw": mw,
        "n_calls": len(calls),
        "season": season,
        "rankings_by_total_loss": rankings,
        "over_call_fixture": {
            "label": "repeated year: one call per day at 44.0 C ambient, idle daily mean 34.0 C, 10 years, nominal set",
            "results": over,
        },
    }
    if out_path is not None:
        write_json(out_path, summary)
        write_json(detail_path, detail)
    return summary


def main(argv=None) -> int:
    from runway.hero import fixture_calls
    from runway.study import CALLS_PATH, read_calls
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    a = ap.parse_args(argv)
    pairs = fixture_calls() if a.fixture or not CALLS_PATH.exists() else read_calls()
    calls = [c for c, _ in pairs]
    summary = run(calls, daily=None if a.fixture else daily_mean_austin())
    print(f"feasible_mw={summary['feasible_mw']} early_replacements={summary['early_replacements']} "
          f"over_call_all_policies_fail={summary['over_call_all_policies_fail']} "
          f"ranking_flipped={summary['ranking_flipped_across_assumption_sets']} (synthetic fleet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
