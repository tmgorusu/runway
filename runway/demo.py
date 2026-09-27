"""The product: `python -m runway.demo --seed 7 --fault offline_wave`.

Reads the offline cache, dispatches one call over the synthetic fleet, injects the
seeded offline wave, re-solves, and writes:
  outputs/track2/metrics.json        requested/delivered MW, shortfall, reserve violations, latency
  outputs/demo/decision_table.csv    units whose setpoint changed on the re-solve
  outputs/demo/run.json              pre-fault and post-fault setpoints, for the dashboard and tests
This is the chaos test: it calls runway.dispatcher.run_chaos, the function
tests/test_dispatcher.py exercises. Fleet size is the ladder's filmed N.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

from runway import dispatcher, ingest
from runway.allocate import cost_name
from runway.contracts import Call
from runway.stub_cost import stub_cost

OUT = ingest.ROOT / "outputs"
DECISION_COLUMNS = ["unit_id", "power_kw", "enclosure_c", "marginal_wear", "assignment"]

try:
    from runway.marginal import marginal_wear as _cost
except ImportError:  # pragma: no cover
    _cost = stub_cost
try:
    from runway.thermal import step_thermal as _step_thermal
except ImportError:  # pragma: no cover
    _step_thermal = None  # Machine does not invent a temperature model


def decision_rows(r: dict) -> list[dict]:
    before = {s.unit_id: s.power_kw for s in r["setpoints_before"]}
    faulted = set(r["faulted"])
    call: Call = r["call"]
    view = {u.unit_id: u for u in r["view_after"]}
    dt_s = call.duration_min * 60.0
    rows = []
    for s in r["setpoints_after"]:
        old = before[s.unit_id]
        if abs(s.power_kw - old) <= 1e-9:
            continue
        u = view[s.unit_id]
        if s.unit_id in faulted:
            assignment = "offline"
        elif old == 0.0:
            assignment = "added"
        elif s.power_kw == 0.0:
            assignment = "dropped"
        else:
            assignment = "increased" if s.power_kw > old else "decreased"
        enclosure = _step_thermal(u, call.ambient_c, s.power_kw, dt_s) if _step_thermal else ""
        rows.append({
            "unit_id": s.unit_id,
            "power_kw": round(s.power_kw, 6),
            "enclosure_c": round(enclosure, 3) if enclosure != "" else "",
            "marginal_wear": round(_cost(u, call, s.power_kw, dt_s), 9),
            "assignment": assignment,
        })
    return rows


def unit_rows(r: dict) -> list[list]:
    """Per unit: id, sun_exposure, health, cost scalar at the post-fault setpoint."""
    call: Call = r["call"]
    dt_s = call.duration_min * 60.0
    after = {s.unit_id: s.power_kw for s in r["setpoints_after"]}
    return [[u.unit_id, round(u.sun_exposure, 4), round(u.health, 4),
             round(_cost(u, call, after[u.unit_id], dt_s), 6)] for u in r["view_after"]]


def run(seed: int = 7, fault: str = "offline_wave", n: int | None = None) -> dict:
    n = n or dispatcher.filmed_n()
    r = dispatcher.run_chaos(seed=seed, fault=fault, n=n)
    m = dispatcher.metrics(r)
    (OUT / "track2").mkdir(parents=True, exist_ok=True)
    (OUT / "demo").mkdir(parents=True, exist_ok=True)
    (OUT / "track2" / "metrics.json").write_text(json.dumps(m, indent=2) + "\n")
    rows = decision_rows(r)
    with open(OUT / "demo" / "decision_table.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=DECISION_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    (OUT / "demo" / "run.json").write_text(json.dumps({
        "synthetic": True,
        "seed": seed,
        "n_units": n,
        "cost": cost_name(),
        "call_id": r["call"].call_id,
        "call_start_utc": r["call"].start.isoformat(),
        "duration_min": r["call"].duration_min,
        "ambient_c": r["call"].ambient_c,
        "faulted": r["faulted"],
        "setpoints_before": [[s.unit_id, s.power_kw] for s in r["setpoints_before"]],
        "setpoints_after": [[s.unit_id, s.power_kw] for s in r["setpoints_after"]],
        "delivered_kw": r["delivered_kw"],
        "solve_ms": r["solve_ms"],
        "units": unit_rows(r),
    }) + "\n")
    from runway import bench, video_script, wear, web

    wear.ensure()  # hero.json, summary.json, blast_overlay.json, PHYSICS_LINES.md (cached on inputs)
    stale = not bench.OUT.exists() or json.loads(bench.OUT.read_text()).get("cost") != cost_name()
    if stale and OUT == ingest.ROOT / "outputs":
        bench.main([])
    web.build()
    video_script.write()
    return {"metrics": m, "rows": rows, "raw": r}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Runway one-command demo (synthetic fleet)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--fault", default="offline_wave", choices=["offline_wave"])
    ap.add_argument("--n", type=int, default=None, help="fleet size; default is the ladder's filmed N")
    args = ap.parse_args(argv)
    out = run(args.seed, args.fault, args.n)
    m = out["metrics"]
    print("SYNTHETIC FLEET. ERCOT dates and 4CP intervals are real; no Base telemetry.")
    print(f"seed={m['seed']}  N={m['n_units']}  cost={m['cost']}  fault={m['fault']} ({m['fraction_offline']:.0%} largest setpoints)")
    print(f"requested  {m['mw_requested']:.3f} MW")
    print(f"delivered  {out['raw']['delivered_kw'] / 1000:.3f} MW   (after re-solve, simulated call time)")
    print(f"shortfall  {m['shortfall_kw'] / 1000:.3f} MW   (reported, never covered from reserve)")
    print(f"reserve_violations = {m['reserve_violations']}")
    print(f"resole_latency_ms  = {m['resole_latency_ms']:.1f}   (wall clock)")
    print(f"seq {m['seq_before']} -> {m['seq_after']}   decision table: {len(out['rows'])} units moved")
    print("wrote outputs/track2/metrics.json, outputs/demo/decision_table.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
