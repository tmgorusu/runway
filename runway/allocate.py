"""Allocate a megawatt call across the fleet.

`even` and `most_charge` delegate to Wear's `runway.policies`. `runway` water-fills
on a marginal-cost scalar: Wear's `marginal_wear` once it imports, otherwise the
labeled stub. Real-time prices are never imported here.

Water-fill: each unit's feasible power is min(p_max_kw, energy_above_reserve_kwh / hours).
On a GRID_POINTS grid of [0, feasible], p_i(lam) is the largest power whose scalar is
at or below lam. Binary-search the smallest lam with sum p_i(lam) >= request, then close
the gap from the previous level proportionally so the sum is exact. If even the full
feasible fleet is short, every unit sits at its cap and the gap is shortfall_kw.
Feasible power never includes reserve energy.
"""

from __future__ import annotations

import numpy as np

from runway import stub_cost as _stub
from runway.contracts import POLICIES, Call, Setpoint, UnitState, feasible_kw

GRID_POINTS = 21

try:  # Wear owns the scalar.
    from runway.marginal import marginal_wear as _marginal_wear
    from runway.marginal import marginal_grid as _marginal_grid
    from runway.marginal import unit_arrays as _unit_arrays
except ImportError:  # pragma: no cover - depends on Wear's progress
    _marginal_wear = _marginal_grid = _unit_arrays = None


def cost_name() -> str:
    return "stub" if _marginal_wear is None else "marginal_wear"


def feasible_caps(units: list[UnitState], hours: float) -> np.ndarray:
    return np.fromiter((feasible_kw(u, hours) for u in units), float, len(units))


def cost_matrix(call: Call, units: list[UnitState], grid: np.ndarray, assumption: str) -> np.ndarray:
    dt_s = call.duration_min * 60.0
    if _marginal_wear is None:
        _stub.announce()
        return _stub.stub_cost_matrix(units, call, grid, dt_s, assumption)
    if _marginal_grid is not None:  # Wear's vectorized marginal_wear; tests pin it to the scalar within 1e-9
        return _marginal_grid(_unit_arrays(units, assumption), call, grid, dt_s, assumption)
    out = np.empty_like(grid)
    for i, u in enumerate(units):
        for j in range(grid.shape[1]):
            out[i, j] = _marginal_wear(u, call, float(grid[i, j]), dt_s, assumption)
    return out


def _level_powers(grid: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """Largest grid power whose cost is at or below the level (0 if none)."""
    idx = grid.shape[1] - 1 - np.argmax(ok[:, ::-1], axis=1)
    return np.where(ok.any(axis=1), grid[np.arange(grid.shape[0]), idx], 0.0)


def water_fill(request_kw: float, caps: np.ndarray, grid: np.ndarray, costs: np.ndarray) -> tuple[np.ndarray, float]:
    request_kw = max(0.0, float(request_kw))
    total_cap = float(caps.sum())
    if total_cap <= request_kw:
        return caps.copy(), request_kw - total_cap
    if request_kw == 0.0:
        return np.zeros_like(caps), 0.0
    levels = np.unique(costs)
    lo, hi = 0, len(levels) - 1  # sum at levels[-1] == total_cap >= request
    while lo < hi:
        mid = (lo + hi) // 2
        if _level_powers(grid, costs <= levels[mid]).sum() >= request_kw:
            hi = mid
        else:
            lo = mid + 1
    top = _level_powers(grid, costs <= levels[lo])
    base = _level_powers(grid, costs <= levels[lo - 1]) if lo > 0 else np.zeros_like(caps)
    base = np.minimum(base, top)
    gap = request_kw - float(base.sum())
    room = top - base
    alpha = gap / float(room.sum()) if room.sum() > 0 else 0.0
    power = base + alpha * room
    power = np.clip(power, 0.0, caps)
    return power, max(0.0, request_kw - float(power.sum()))


def allocate(call, units, policy, assumption="nominal") -> tuple[list[Setpoint], float]:
    """policy 'even' and 'most_charge' delegate to Wear.policies.
    policy 'runway' water-fills on a cost scalar.
    Returns (setpoints, shortfall_kw) with shortfall_kw >= 0.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; expected one of {POLICIES}")
    if policy != "runway":
        from runway import policies  # Wear's module

        return getattr(policies, policy)(call, units)
    caps = feasible_caps(units, call.hours)
    grid = caps[:, None] * np.linspace(0.0, 1.0, GRID_POINTS)[None, :]
    costs = cost_matrix(call, units, grid, assumption)
    power, shortfall = water_fill(call.kw_requested, caps, grid, costs)
    setpoints = [Setpoint(u.unit_id, float(p), call.call_id) for u, p in zip(units, power)]
    return setpoints, float(shortfall)
