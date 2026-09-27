"""The wear scalar the runway policy water-fills on.

W(P) is the wear over the call at constant power P minus the wear at zero power
over the same window, integrated with 3-point Gauss-Legendre on the exact
enclosure temperature path. The scalar is the forward difference of W times the
glide-path weight, doubled when telemetry is stale on a high peak score.

W is convex in P under the loader constraints, so the forward difference is
nondecreasing in P.
"""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime

import numpy as np

from runway.aging import age, age_arrays, unit_efc
from runway.assumptions import load
from runway.contracts import REPLACEMENT_HEALTH
from runway.thermal import step_thermal, steady_state_c, temps_at

GL3_X = (-math.sqrt(3.0 / 5.0), 0.0, math.sqrt(3.0 / 5.0))
GL3_W = (5.0 / 9.0, 8.0 / 9.0, 5.0 / 9.0)


def node_times(dt_s: float) -> list[float]:
    return [0.5 * dt_s * (x + 1.0) for x in GL3_X]


def glide_weight(p: dict, health, age_years, term_years):
    target = 1.0 - (1.0 - REPLACEMENT_HEALTH) * (np.asarray(age_years) / np.asarray(term_years))
    gap = target - np.asarray(health)
    return np.exp(p["glide_k"] * gap)


def _epoch(ts: datetime) -> float:
    if ts.tzinfo is None:
        raise TypeError("timestamps must be timezone-aware UTC on the simulated clock")
    return ts.timestamp()


def is_stale(p: dict, call, last_seen) -> bool:
    if last_seen is None:
        return False
    return (_epoch(call.start) - _epoch(last_seen)) > p["stale_after_s"] and call.peak_odds >= p["stale_peak_odds"]


def marginal_wear(u, call, power_kw, dt_s, assumption="nominal") -> float:
    """One positive scalar. Higher means more costly to dispatch this unit at this power.
    dt_s is the time the power is held, call.duration_min * 60, not a substep.
    """
    p = load(assumption)
    amb = call.ambient_c
    u0 = replace(u, enclosure_c=steady_state_c(amb, u.sun_exposure, 0.0, assumption))

    def wear(power):
        total = 0.0
        for t, w in zip(node_times(dt_s), GL3_W):
            hot = age(u0, step_thermal(u0, amb, power, t, assumption), power, dt_s, assumption)
            idle = age(u0, step_thermal(u0, amb, 0.0, t, assumption), 0.0, dt_s, assumption)
            total += 0.5 * w * (hot - idle)
        return total

    h = p["fd_step_kw"]
    slope = (wear(power_kw + h) - wear(power_kw)) / h
    weight = float(glide_weight(p, u.health, u.age_years, u.term_years))
    stale = p["stale_multiplier"] if is_stale(p, call, u.last_seen) else 1.0
    return slope * weight * stale


# ---- vectorized paths used by allocate, hero, and replay -------------------

def unit_arrays(units, assumption: str = "nominal") -> dict:
    p = load(assumption)
    last = [(_epoch(u.last_seen) if u.last_seen is not None else np.nan) for u in units]
    return {
        "unit_id": np.array([u.unit_id for u in units], dtype=np.int64),
        "soc": np.array([u.soc for u in units], dtype=float),
        "health": np.array([u.health for u in units], dtype=float),
        "age_years": np.array([u.age_years for u in units], dtype=float),
        "term_years": np.array([u.term_years for u in units], dtype=float),
        "sun": np.array([u.sun_exposure for u in units], dtype=float),
        "efc": np.array([unit_efc(u, p) for u in units], dtype=float),
        "last_seen_s": np.array(last, dtype=float),
    }


def _col(a, ndim):
    a = np.asarray(a, dtype=float)
    return a.reshape(a.shape + (1,) * (ndim - a.ndim)) if ndim > a.ndim else a


def call_wear(arr: dict, ambient_c: float, power_kw, dt_s: float, assumption="nominal",
              attributable: bool = True, t0_c=None):
    """Wear per unit over a constant-power segment. Shape follows power_kw ([N] or [N, K])."""
    p = load(assumption)
    power_kw = np.asarray(power_kw, dtype=float)
    nd = power_kw.ndim
    sun = _col(arr["sun"], nd)
    soc, age_y, efc = (_col(arr[k], nd) for k in ("soc", "age_years", "efc"))
    t0 = steady_state_c(ambient_c, sun, 0.0, assumption) if t0_c is None else _col(t0_c, nd)
    total = 0.0
    for t, w in zip(node_times(dt_s), GL3_W):
        temp = temps_at(t0, ambient_c, sun, power_kw, t, assumption)
        seg = age_arrays(p, temp, power_kw, dt_s, soc, age_y, efc)
        if attributable:
            idle_temp = temps_at(t0, ambient_c, sun, 0.0, t, assumption)
            seg = seg - age_arrays(p, idle_temp, 0.0, dt_s, soc, age_y, efc)
        total = total + 0.5 * w * seg
    return total


def weights(arr: dict, call, assumption="nominal"):
    p = load(assumption)
    g = glide_weight(p, arr["health"], arr["age_years"], arr["term_years"])
    start = _epoch(call.start)
    stale = ((start - arr["last_seen_s"]) > p["stale_after_s"]) & (call.peak_odds >= p["stale_peak_odds"])
    return g * np.where(stale, p["stale_multiplier"], 1.0)


def marginal_grid(arr: dict, call, power_kw, dt_s: float, assumption="nominal"):
    """marginal_wear for every unit (rows) at every power (columns), vectorized."""
    p = load(assumption)
    power_kw = np.asarray(power_kw, dtype=float)
    h = p["fd_step_kw"]
    hi = call_wear(arr, call.ambient_c, power_kw + h, dt_s, assumption, attributable=False)
    lo = call_wear(arr, call.ambient_c, power_kw, dt_s, assumption, attributable=False)
    slope = (hi - lo) / h
    return slope * _col(weights(arr, call, assumption), power_kw.ndim)
