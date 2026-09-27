"""Wear's stage of the one-command demo: hero, BLAST overlay, replay, physics lines.

`python -m runway.wear` rebuilds everything. `ensure()` rebuilds only when the
calls, the calling rule, the assumption files, or the BLAST reference changed,
so the demo stays fast on repeat runs.
"""
from __future__ import annotations

import hashlib
import sys

from runway.paths import ASSUMPTIONS, CALLING_RULE, OUTPUTS, PHYSICS, read_json, write_json

STAMP = OUTPUTS / "wear_inputs.json"


def _inputs() -> dict:
    from runway import calendar
    from runway import telemetry
    from runway.paths import DATA
    files = [calendar.CALLS, CALLING_RULE, PHYSICS / "blast_lite_reference.csv", telemetry.MANIFEST, DATA / "manifest.json",
             *sorted(ASSUMPTIONS.glob("*.json"))]
    return {str(f.relative_to(CALLING_RULE.parent)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in files if f.exists()}


def run() -> dict:
    from runway import calendar, hero, overlay, physics_lines, replay, telemetry_fit
    from runway.study import daily_mean_austin, read_calls
    if not calendar.CALLS.exists():
        calendar.run()
    fit = telemetry_fit.run()
    h = hero.run()
    ov = overlay.build()
    s = replay.run([c for c, _ in read_calls()], daily=daily_mean_austin())
    physics_lines.write()
    from runway import austin
    aus = austin.build()
    write_json(STAMP, _inputs())
    return {"hero": h, "overlay": ov, "summary": s, "telemetry_fit": fit, "austin_events": len(aus["events"])}


def ensure() -> bool:
    """Rebuild Wear's artifacts if any input changed. Returns True when it rebuilt."""
    from runway import calendar
    if not calendar.CALLS.exists():
        calendar.run()
    fresh = STAMP.exists() and read_json(STAMP) == _inputs() and all(
        (OUTPUTS / p).exists() for p in ("track1/hero.json", "replay/summary.json", "physics/blast_overlay.json",
                                         "telemetry/fleet_estimates.parquet", "physics/telemetry_fit.json",
                                         "austin/events.json"))
    if not fresh:
        run()
    else:
        from runway import physics_lines
        physics_lines.write()
    return not fresh


def main() -> int:
    out = run()
    n = out["hero"]["assumption_sets"]["nominal"]
    s = out["summary"]
    print(f"hero ratio={n['ratio']:.4f} wear_fraction_on_misses={n['wear_fraction_on_misses']:.4f} (even spread, synthetic fleet)")
    print(f"blast overlay status={out['overlay']['status']}")
    f = out["telemetry_fit"]
    print(f"telemetry fit (synthetic Base-like telemetry): exposure corr={f['exposure_corr']:.3f} "
          f"mae={f['exposure_mae']:.3f} health mae={f['health_mae']:.4f} pass={f['pass']}")
    print(f"replay feasible_mw={s['feasible_mw']} early_replacements={s['early_replacements']} "
          f"over_call_all_policies_fail={s['over_call_all_policies_fail']} ranking_flipped={s['ranking_flipped_across_assumption_sets']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
