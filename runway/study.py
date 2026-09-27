"""Whole-fleet helpers for Wear's offline studies (hero, replay).

Reads Machine's handoff/calls.parquet and cached weather. The runway policy goes
through Machine's `allocate.water_fill` on Wear's vectorized marginal_wear grid,
so Wear does not ship a second water-fill.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import numpy as np

from runway import policies
from runway.allocate import GRID_POINTS, water_fill
from runway.contracts import Call, feasible_kw
from runway.marginal import marginal_grid
from runway.paths import HANDOFF

CALLS_PATH = HANDOFF / "calls.parquet"
REPLAY_START = datetime(2025, 6, 1, 5, 0, tzinfo=timezone.utc)  # fleet ages are as of this instant


def dt_s(call: Call) -> float:
    return call.duration_min * 60.0


def caps_for(units, hours: float) -> np.ndarray:
    return np.array([feasible_kw(u, hours) for u in units], dtype=float)


def aged_to(units, when: datetime, soc: float | None = None):
    """Copies with age_years advanced from REPLAY_START to `when`, no telemetry, optional SOC reset."""
    dy = (when - REPLAY_START).total_seconds() / (365.25 * 86400.0)
    out = []
    for u in units:
        v = dataclasses.replace(u, age_years=u.age_years + dy, last_seen=None, metadata=dict(u.metadata))
        if soc is not None:
            v.soc = soc
        out.append(v)
    return out


def allocate_arrays(call: Call, arr: dict, caps: np.ndarray, policy: str, assumption: str = "nominal",
                    request_kw: float | None = None):
    request_kw = call.kw_requested if request_kw is None else request_kw
    if policy == "even":
        return policies.even_arrays(caps, arr["unit_id"], request_kw)
    if policy == "most_charge":
        return policies.most_charge_arrays(caps, arr["soc"], arr["unit_id"], request_kw)
    if policy != "runway":
        raise ValueError(f"unknown policy {policy!r}")
    grid = caps[:, None] * np.linspace(0.0, 1.0, GRID_POINTS)[None, :]
    costs = marginal_grid(arr, call, grid, dt_s(call), assumption)
    power, short = water_fill(request_kw, caps, grid, costs)
    return power, policies.shortfall(request_kw, float(power.sum()))


def read_calls(path=CALLS_PATH) -> list[tuple[Call, bool]]:
    import pandas as pd
    df = pd.read_parquet(path)
    out = []
    for r in df.itertuples(index=False):
        start = r.start.to_pydatetime() if hasattr(r.start, "to_pydatetime") else r.start
        out.append((Call(r.call_id, start, float(r.duration_min), float(r.mw_requested), r.utility,
                         float(r.peak_odds), float(r.ambient_c)), bool(r.hit)))
    return out


def rule_hash_of_calls(path=CALLS_PATH) -> str:
    import pandas as pd
    hashes = set(pd.read_parquet(path, columns=["rule_hash"])["rule_hash"])
    return hashes.pop() if len(hashes) == 1 else ""


def daily_mean_austin() -> dict:
    """Daily mean Austin temperature (°C) by America/Chicago date, from the cached weather."""
    from runway import ingest
    w = ingest.load("weather")
    w = w[w["city"] == "Austin"].copy()
    w["day"] = w["ts_utc"].dt.tz_convert("America/Chicago").dt.date
    return {d: float(v) for d, v in w.groupby("day")["temp_c"].mean().items()}
