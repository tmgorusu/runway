"""Compares aging.age against the committed BLAST-Lite reference run.

Pre-registered reference points and bands (WEAR council): calendar at 25 and
45 °C, SOC 1.0, 365 days, within 2%; cycle at 25 °C, DOD 0.7, 0.25 C, 1,000 EFC,
within 5%. Cycle at 45 °C has no band: it records how far the replaced
temperature term moves us from BLAST.
"""
from __future__ import annotations

import csv
from dataclasses import replace

from runway.aging import age
from runway.contracts import CAPACITY_KWH, UnitState
from runway.paths import OUTPUTS, PHYSICS, write_json

REFERENCE_CSV = PHYSICS / "blast_lite_reference.csv"
OVERLAY_PATH = OUTPUTS / "physics" / "blast_overlay.json"
BANDS = {"calendar_25c_365d": 0.02, "calendar_45c_365d": 0.02, "cycle_25c_1000efc": 0.05}


def read_reference(path=REFERENCE_CSV) -> list[dict]:
    with open(path) as f:
        return [{k: (v if k == "kind" else float(v)) for k, v in row.items()} for row in csv.DictReader(f)]


def ours_calendar(temp_c: float, days: int = 365) -> list[float]:
    u = UnitState(0, soc=1.0, age_years=0.0, metadata={"efc": 0.0})
    lost, out = 0.0, []
    for _ in range(days):
        lost += age(u, temp_c, 0.0, 86400.0)
        u = replace(u, age_years=u.age_years + 1.0 / 365.25)
        out.append(lost)
    return out


def ours_cycle(temp_c: float, efc_targets: list[float], c_rate: float = 0.25, dod: float = 0.7) -> list[float]:
    """Cycle-only loss along the same 1.0 <-> 0.3 SOC profile, integrated with age() per half cycle."""
    power = c_rate * CAPACITY_KWH
    half_s = dod / c_rate * 3600.0
    u = UnitState(0, soc=1.0, age_years=0.0, metadata={"efc": 0.0})
    lost, out, i = 0.0, [], 0
    efc = 0.0
    for target in efc_targets:
        while efc < target - 1e-9:
            lost += age(u, temp_c, power, half_s) - age(u, temp_c, 0.0, half_s)
            efc += power * half_s / 3600.0 / (2.0 * CAPACITY_KWH)
            u = replace(u, metadata={"efc": efc}, age_years=u.age_years + half_s / (365.25 * 86400.0))
        out.append(lost)
        i += 1
    return out


def build(path=REFERENCE_CSV, out_path=OVERLAY_PATH) -> dict:
    if not path.exists():
        result = {"status": "failed", "reason": "blast_reference_missing"}
        write_json(out_path, result)
        return result
    rows = read_reference(path)
    cal = {t: [r for r in rows if r["kind"] == "calendar" and r["temp_c"] == t] for t in (25.0, 35.0, 45.0)}
    cyc = {t: [r for r in rows if r["kind"] == "cycle" and r["temp_c"] == t] for t in (25.0, 45.0)}
    series = {"calendar": {}, "cycle": {}}
    for t, rs in cal.items():
        ours = ours_calendar(t, len(rs))
        series["calendar"][f"{t:g}"] = [{"days": r["days"], "blast": r["loss_time"], "ours": o}
                                        for r, o in zip(rs[::7], ours[::7])]
        cal[t] = (rs[-1]["loss_time"], ours[-1])
    for t, rs in cyc.items():
        targets = [r["efc"] for r in rs]
        ours = ours_cycle(t, targets)
        series["cycle"][f"{t:g}"] = [{"efc": r["efc"], "blast": r["loss_efc"], "ours": o}
                                     for r, o in zip(rs[::20], ours[::20])]
        cyc[t] = (rs[-1]["loss_efc"], ours[-1])
    points = {
        "calendar_25c_365d": cal[25.0],
        "calendar_45c_365d": cal[45.0],
        "cycle_25c_1000efc": cyc[25.0],
    }
    checks = {}
    for name, (blast, ours) in points.items():
        rel = abs(ours - blast) / blast
        checks[name] = {"blast": blast, "ours": ours, "relative_error": rel, "band": BANDS[name], "pass": rel <= BANDS[name]}
    result = {
        "status": "ok",
        "synthetic_fleet": True,
        "reference": "BLAST-Lite 1.1.1 Lfp_Gr_250AhPrismatic, run by physics/make_blast_reference.py",
        "free_parameters_fit": 0,
        "checks": checks,
        "cycle_45c_ours_over_blast": cyc[45.0][1] / cyc[45.0][0],
        "cycle_45c_note": "BLAST's 250Ah cycle term falls with temperature (fit artifact per its docstring); ours uses Arrhenius 31.7 kJ/mol.",
        "calendar_35c_ours_over_blast": cal[35.0][1] / cal[35.0][0],
        "series": series,
    }
    write_json(out_path, result)
    return result


if __name__ == "__main__":
    r = build()
    print(r["status"], {k: round(v["relative_error"], 4) for k, v in r.get("checks", {}).items()})
