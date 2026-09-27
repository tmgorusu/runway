"""Writes physics/blast_lite_reference.csv from BLAST-Lite 1.1.1 (NREL, Apache-2.0).

Run with a Python <= 3.13 environment that has blast-lite installed, e.g.
    .venv-blast/bin/python physics/make_blast_reference.py
The runway package never imports BLAST-Lite; tests read the committed CSV.

Rows:
  calendar: SOC 1.0 held at 25, 35, 45 °C; capacity lost to time (q_t) daily for 365 days.
  cycle: 1.0 <-> 0.3 SOC at 0.25 C (DOD 0.7) at 25 and 45 °C; capacity lost to throughput
         (q_EFC) after each cycle, to 1,000 EFC.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from blast import __name__ as _blast  # noqa: F401
from blast.models import Lfp_Gr_250AhPrismatic

OUT = Path(__file__).resolve().parent / "blast_lite_reference.csv"


def calendar(temp_c: float, days: int = 365):
    m = Lfp_Gr_250AhPrismatic()
    rows = []
    for d in range(days):
        t = np.array([d * 86400.0, (d + 1) * 86400.0])
        m.update_battery_state(t, np.array([1.0, 1.0]), np.array([temp_c, temp_c]))
        rows.append(("calendar", temp_c, 1.0, 0.0, 0.0, d + 1, float(m.stressors["efc"][-1]), 1.0 - float(m.outputs["q_t"][-1]), 1.0 - float(m.outputs["q_EFC"][-1])))
    return rows


def cycle(temp_c: float, target_efc: float = 1000.0, c_rate: float = 0.25, dod: float = 0.7):
    m = Lfp_Gr_250AhPrismatic()
    half_h = dod / c_rate
    n = 60
    t_half = np.linspace(0.0, half_h * 3600.0, n + 1)
    soc_down = np.linspace(1.0, 1.0 - dod, n + 1)
    rows = []
    t0 = 0.0
    efc = 0.0
    while efc < target_efc:
        t = np.concatenate([t0 + t_half, t0 + t_half[-1] + t_half[1:]])
        soc = np.concatenate([soc_down, soc_down[::-1][1:]])
        m.update_battery_state(t, soc, np.full(len(t), temp_c))
        t0 = float(t[-1])
        efc = float(m.stressors["efc"][-1])
        rows.append(("cycle", temp_c, c_rate, dod, efc, t0 / 86400.0, efc, 1.0 - float(m.outputs["q_t"][-1]), 1.0 - float(m.outputs["q_EFC"][-1])))
    return rows


def main():
    rows = []
    for temp in (25.0, 35.0, 45.0):
        rows += calendar(temp)
    for temp in (25.0, 45.0):
        rows += cycle(temp)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "temp_c", "c_rate", "dod", "efc_target", "days", "efc", "loss_time", "loss_efc"])
        for r in rows:
            w.writerow([r[0]] + [repr(float(x)) for x in r[1:]])
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
