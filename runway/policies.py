"""Baseline policies. Both clip every unit to min(p_max_kw, energy_above_reserve_kwh / hours).

Machine's `allocate` calls `even(call, units)` and `most_charge(call, units)`. The
array versions are what hero and replay use on whole fleets.
"""
from __future__ import annotations

import numpy as np

from runway.contracts import Setpoint, feasible_kw

SHORTFALL_EPS_KW = 1e-9


def shortfall(request_kw: float, delivered_kw: float) -> float:
    gap = request_kw - delivered_kw
    return gap if gap > SHORTFALL_EPS_KW else 0.0


def _fill_in_order(caps: np.ndarray, start: np.ndarray, order: np.ndarray, residual: float) -> np.ndarray:
    out = start.copy()
    if residual <= 0:
        return out
    head = (caps - start)[order]
    cum = np.cumsum(head)
    take = np.clip(residual - (cum - head), 0.0, head)
    out[order] += take
    return out


def even_arrays(caps, unit_ids, request_kw: float):
    """Equal kilowatts, then residual headroom filled in unit_id order."""
    caps = np.asarray(caps, dtype=float)
    n = len(caps)
    if n == 0:
        return caps.copy(), max(0.0, request_kw)
    p = np.minimum(caps, request_kw / n)
    p = _fill_in_order(caps, p, np.argsort(unit_ids, kind="stable"), request_kw - float(p.sum()))
    return p, shortfall(request_kw, float(p.sum()))


def most_charge_arrays(caps, soc, unit_ids, request_kw: float):
    """Fill highest SOC first, unit_id breaking ties, each to its cap."""
    caps = np.asarray(caps, dtype=float)
    order = np.lexsort((np.asarray(unit_ids), -np.asarray(soc, dtype=float)))
    p = _fill_in_order(caps, np.zeros_like(caps), order, request_kw)
    return p, shortfall(request_kw, float(p.sum()))


def _caps(units, hours):
    return np.array([feasible_kw(u, hours) for u in units], dtype=float)


def _setpoints(call, units, p):
    return [Setpoint(u.unit_id, float(x), call.call_id) for u, x in zip(units, p)]


def even(call, units):
    p, short = even_arrays(_caps(units, call.hours), [u.unit_id for u in units], call.kw_requested)
    return _setpoints(call, units, p), short


def most_charge(call, units):
    p, short = most_charge_arrays(_caps(units, call.hours), [u.soc for u in units], [u.unit_id for u in units],
                                  call.kw_requested)
    return _setpoints(call, units, p), short
