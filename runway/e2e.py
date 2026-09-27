"""Quarter milestone: one call on the published June 2025 4CP interval, 32 synthetic units.

`python -m runway.e2e --offline` reads only data/fixture/. The call is centered on
the published interval the way the calling rule centers on a forecast peak: it
starts 45 minutes before the interval ends and lasts 90 minutes. Requested MW
scales with fleet size as 40 * N / 4000.
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from runway import ingest
from runway.allocate import allocate, cost_name
from runway.calendar import ambient_at
from runway.contracts import Call
from runway.fleet import make_fleet

OUT = ingest.ROOT / "outputs" / "m1" / "dispatch.json"


def june_call(n_units: int) -> Call:
    cp = ingest.load("published_4cp", fixture=True).iloc[0]
    end = cp["interval_end_utc"]
    start = end - pd.Timedelta(minutes=45)
    weather = ingest.load("weather", fixture=True)
    return Call(
        call_id=f"published-4cp-{end.tz_convert('America/Chicago').date().isoformat()}",
        start=start.to_pydatetime(),
        duration_min=90,
        mw_requested=40.0 * n_units / 4000,
        utility="Austin Energy",
        peak_odds=1.0,
        ambient_c=ambient_at(weather, start, start + pd.Timedelta(minutes=90)),
    )


def run(n_units: int = 32, seed: int = 7, policy: str = "runway") -> dict:
    call = june_call(n_units)
    units = make_fleet(n_units, seed)
    setpoints, shortfall = allocate(call, units, policy)
    return {
        "synthetic": True,
        "seed": seed,
        "n_units": n_units,
        "policy": policy,
        "cost": cost_name(),
        "call": {
            "call_id": call.call_id,
            "start": call.start.isoformat(),
            "duration_min": call.duration_min,
            "mw_requested": call.mw_requested,
            "ambient_c": round(call.ambient_c, 3),
        },
        "setpoints": [{"unit_id": s.unit_id, "power_kw": round(s.power_kw, 6)} for s in setpoints],
        "delivered_kw": round(sum(s.power_kw for s in setpoints), 6),
        "shortfall_kw": round(shortfall, 6),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="read only the committed fixture (always true)")
    ap.add_argument("--n", type=int, default=32)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    out = run(args.n, args.seed)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    c = out["call"]
    print(f"synthetic fleet  seed={out['seed']}  N={out['n_units']}  cost={out['cost']}")
    print(f"call_id={c['call_id']}  start={c['start']}  {c['duration_min']} min  {c['mw_requested']} MW  ambient={c['ambient_c']} C")
    for s in out["setpoints"]:
        print(f"  unit {s['unit_id']:>3}  {s['power_kw']:9.4f} kW")
    print(f"delivered_kw={out['delivered_kw']}  shortfall_kw={out['shortfall_kw']}")
    print(f"wrote {OUT.relative_to(ingest.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
