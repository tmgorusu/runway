"""Track 1 hero: wear per kWh on missed calls over wear per kWh on hits, under even spread.

Each call starts at SOC 1.0 on the 4,000-unit synthetic fleet (seed 7). Wear
per kWh uses call-attributable wear: wear during the call minus wear at zero
power over the same window. total_capacity_fraction_lost is total loss over the
call windows, calendar included, for each policy on the nominal set.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import numpy as np

from runway.assumptions import SETS
from runway.contracts import Call
from runway.fleet import make_fleet
from runway.marginal import call_wear, unit_arrays
from runway.paths import OUTPUTS, rule_hash, write_json
from runway.study import CALLS_PATH, aged_to, allocate_arrays, caps_for, dt_s, read_calls, rule_hash_of_calls
from runway.thermal import steady_state_c, temps_at

HERO_PATH = OUTPUTS / "track1" / "hero.json"
REFERENCE_PATH = OUTPUTS / "physics" / "reference_pair.json"
GLIDE_PATH = OUTPUTS / "physics" / "glide_sensitivity.json"
FLEET_N = 4000
SEED = 7
SOC_EACH_CALL = 1.0


def fixture_calls() -> list[tuple[Call, bool]]:
    """Four-row fixture with the calls.parquet columns, used until the real parquet exists."""
    rows = [("2025-06-20", 36.0, True), ("2025-07-15", 38.5, False), ("2025-08-12", 39.5, True), ("2025-09-03", 35.0, False)]
    return [(Call(f"{d}-fixture", datetime.fromisoformat(d + "T22:15:00+00:00"), 90.0, 40.0, "Austin Energy", 0.99, t), h)
            for d, t, h in rows]


def _prepare(units, call):
    aged = aged_to(units, call.start, soc=SOC_EACH_CALL)
    return aged, unit_arrays(aged), caps_for(aged, call.hours)


def compute(calls_hits: list[tuple[Call, bool]], n: int = FLEET_N, seed: int = SEED, rule: str = "") -> dict:
    calls = [c for c, _ in calls_hits]
    hits = [h for _, h in calls_hits]
    fleet = make_fleet(n, seed)
    blocks = {}
    prepared = [_prepare(fleet, c) for c in calls]
    even_power = [allocate_arrays(c, arr, caps, "even")[0] for c, (_, arr, caps) in zip(calls, prepared)]
    for name in SETS:
        wear = {True: 0.0, False: 0.0}
        kwh = {True: 0.0, False: 0.0}
        for c, hit, (_, arr, _caps), p in zip(calls, hits, prepared, even_power):
            w = call_wear(arr, c.ambient_c, p, dt_s(c), name, attributable=True)
            wear[hit] += float(np.sum(w))
            kwh[hit] += float(np.sum(p) * c.hours)
        wpk_miss = wear[False] / kwh[False] if kwh[False] else float("nan")
        wpk_hit = wear[True] / kwh[True] if kwh[True] else float("nan")
        blocks[name] = {
            "wear_per_kwh_miss": wpk_miss,
            "wear_per_kwh_hit": wpk_hit,
            "ratio": wpk_miss / wpk_hit if kwh[True] and kwh[False] else float("nan"),
            "wear_fraction_on_misses": wear[False] / (wear[False] + wear[True]),
            "kwh_miss": kwh[False],
            "kwh_hit": kwh[True],
        }
    totals = {}
    for policy in ("runway", "even", "most_charge"):
        total = 0.0
        for c, (_, arr, caps) in zip(calls, prepared):
            p = allocate_arrays(c, arr, caps, policy)[0]
            total += float(np.sum(call_wear(arr, c.ambient_c, p, dt_s(c), "nominal", attributable=False)))
        totals[policy] = total
    return {
        "synthetic": True,
        "fleet_n": n,
        "seed": seed,
        "policy_for_ratio": "even",
        "rule_hash": rule,
        "soc_each_call": SOC_EACH_CALL,
        "assumption_sets": blocks,
        "total_capacity_fraction_lost": totals,
    }


def reference_pair(calls_hits: list[tuple[Call, bool]]) -> dict:
    """Two synthetic units, same age and term, same 15 kWh, sun 0 versus sun 1, on the hottest call."""
    call = max((c for c, _ in calls_hits), key=lambda c: (c.ambient_c, c.call_id))
    power = 10.0
    out = {"synthetic_fleet": True, "call_id": call.call_id, "ambient_c": call.ambient_c,
           "power_kw": power, "kwh": power * call.hours, "units": []}
    from runway.aging import initial_health
    for sun in (0.0, 1.0):
        arr = {"sun": np.array([sun]), "soc": np.array([1.0]), "age_years": np.array([5.0]),
               "efc": np.array([500.0])}
        t0 = steady_state_c(call.ambient_c, sun, 0.0)
        t_end = float(temps_at(t0, call.ambient_c, sun, power, dt_s(call)))
        w = float(call_wear(arr, call.ambient_c, np.array([power]), dt_s(call), "nominal")[0])
        out["units"].append({"sun_exposure": sun, "age_years": 5.0, "health": initial_health(5.0, 10),
                             "enclosure_start_c": float(t0), "enclosure_end_c": t_end,
                             "capacity_fraction_lost": w, "reserve_touched": False})
    out["ratio_sun1_over_sun0"] = out["units"][1]["capacity_fraction_lost"] / out["units"][0]["capacity_fraction_lost"]
    return out


def glide_sensitivity(calls_hits: list[tuple[Call, bool]], n: int = FLEET_N, seed: int = SEED) -> dict:
    """Total wear over the hero calls for each policy: nominal (glide off) versus the glide-weight sensitivity run."""
    from runway.assumptions import load
    k = load("nominal")["glide_k_sensitivity"]
    calls = [c for c, _ in calls_hits]
    fleet = make_fleet(n, seed)
    prepared = [_prepare(fleet, c) for c in calls]
    out = {"synthetic_fleet": True, "fleet_n": n, "seed": seed, "cost_assumption": {}, "wear_measured_with": "nominal"}
    for label, cost_set in (("nominal_glide_k_0", "nominal"), (f"sensitivity_glide_k_{k:g}", f"nominal+glide_k={k:g}")):
        totals = {}
        for policy in ("runway", "even", "most_charge"):
            totals[policy] = sum(
                float(np.sum(call_wear(arr, c.ambient_c, allocate_arrays(c, arr, caps, policy, cost_set)[0],
                                       dt_s(c), "nominal", attributable=False)))
                for c, (_, arr, caps) in zip(calls, prepared))
        out["cost_assumption"][label] = {
            "total_capacity_fraction_lost": totals,
            "runway_minus_even_pct": 100.0 * (totals["runway"] - totals["even"]) / totals["even"],
        }
    return out


def run(calls_path=None, fixture: bool = False, out_path=HERO_PATH, n: int = FLEET_N) -> dict:
    path = calls_path or CALLS_PATH
    if fixture or not path.exists():
        calls, rule = fixture_calls(), ""
    else:
        calls = read_calls(path)
        digest = rule_hash_of_calls(path)
        rule = digest if digest == rule_hash() else ""
    hero = compute(calls, n=n, rule=rule)
    write_json(out_path, hero)
    if out_path == HERO_PATH:
        write_json(REFERENCE_PATH, reference_pair(calls))
        write_json(GLIDE_PATH, glide_sensitivity(calls, n=n))
    return hero


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    a = ap.parse_args(argv)
    hero = run(fixture=a.fixture)
    for name, b in hero["assumption_sets"].items():
        print(f"{name:17s} ratio={b['ratio']:.4f} wear_fraction_on_misses={b['wear_fraction_on_misses']:.4f}")
    print("synthetic fleet; policy_for_ratio=even")
    return 0


if __name__ == "__main__":
    sys.exit(main())
