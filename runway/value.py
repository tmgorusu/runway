"""What the 2025 hunt was worth and what it cost. Writes outputs/track1/value.json.

Dollars come only from the hackathon brief: about $17/kW per 4CP interval, a rough ERCOT-wide
figure. The cost side is battery life: call-attributable capacity lost across the synthetic
4,000-unit fleet under even spread, in battery-equivalents (the sum of each unit's capacity
fraction lost). No battery price is assumed; the dashboard lets a user enter one.

The call-rule trade-off runs the committed 0.97 threshold beside the 0.96 / 0.98 pair that
CALLING_RULE.md allows in a side file. The rule itself is not changed.
"""
from __future__ import annotations

import sys

import numpy as np

from runway import calendar
from runway.contracts import Call
from runway.fleet import make_fleet
from runway.hero import SOC_EACH_CALL
from runway.marginal import call_wear, unit_arrays
from runway.paths import OUTPUTS, write_json
from runway.study import aged_to, allocate_arrays, caps_for, dt_s

VALUE_PATH = OUTPUTS / "track1" / "value.json"
USD_PER_KW_INTERVAL = 17.0  # hackathon brief, rough ERCOT-wide
CONTRACT_MW = 40.0
INTERVALS_PER_YEAR = 4
THRESHOLDS = (0.96, 0.97, 0.98)


def _calls(threshold: float):
    df, flags = calendar.run(threshold=threshold, write=False)
    calls = [(Call(r.call_id, r.start.to_pydatetime(), float(r.duration_min), float(r.mw_requested), r.utility,
                   float(r.peak_odds), float(r.ambient_c)), bool(r.hit)) for r in df.itertuples()]
    return calls, sum(f["hit"] for f in flags)


def call_wear_even(calls, fleet) -> dict:
    wear = {True: 0.0, False: 0.0}
    kwh = {True: 0.0, False: 0.0}
    for call, hit in calls:
        units = aged_to(fleet, call.start, soc=SOC_EACH_CALL)
        arr, caps = unit_arrays(units), caps_for(units, call.hours)
        p = allocate_arrays(call, arr, caps, "even")[0]
        wear[hit] += float(np.sum(call_wear(arr, call.ambient_c, p, dt_s(call))))
        kwh[hit] += float(np.sum(p) * call.hours)
    return {"wear_hit": wear[True], "wear_miss": wear[False], "kwh_hit": kwh[True], "kwh_miss": kwh[False]}


def build() -> dict:
    fleet = make_fleet(4000)
    rows = []
    for t in THRESHOLDS:
        calls, caught = _calls(t)
        w = call_wear_even(calls, fleet)
        total = w["wear_hit"] + w["wear_miss"]
        rows.append({
            "threshold": t,
            "committed": t == 0.97,
            "calls": len(calls),
            "intervals_caught": caught,
            "value_caught_usd": USD_PER_KW_INTERVAL * CONTRACT_MW * 1000.0 * caught,
            "discharge_mwh": (w["kwh_hit"] + w["kwh_miss"]) / 1000.0,
            "wear_battery_eq": total,
            "wear_on_misses_battery_eq": w["wear_miss"],
            "wear_share_on_misses": w["wear_miss"] / total if total else 0.0,
            "wear_per_interval_caught": total / caught if caught else None,
        })
    committed = next(r for r in rows if r["committed"])
    out = {
        "synthetic_fleet": True,
        "sources": {"usd_per_kw_interval": "hackathon brief, rough ERCOT-wide figure",
                    "calls": "handoff/calls.parquet under CALLING_RULE.md; 0.96 and 0.98 are the allowed sensitivity pair",
                    "wear": "call-attributable capacity lost, even spread, nominal assumptions, 4,000 synthetic units"},
        "usd_per_kw_interval": USD_PER_KW_INTERVAL,
        "contract_mw": CONTRACT_MW,
        "value_at_stake_usd": USD_PER_KW_INTERVAL * CONTRACT_MW * 1000.0 * INTERVALS_PER_YEAR,
        "committed": committed,
        "thresholds": rows,
    }
    write_json(VALUE_PATH, out)
    return out


def main() -> int:
    out = build()
    for r in out["thresholds"]:
        print(f"threshold {r['threshold']:.2f}{' (committed)' if r['committed'] else ''}: {r['calls']} calls, "
              f"{r['intervals_caught']}/4 caught, ${r['value_caught_usd']/1e6:.2f}M, wear {r['wear_battery_eq']:.2f} battery-eq "
              f"({100 * r['wear_share_on_misses']:.1f}% on misses)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
