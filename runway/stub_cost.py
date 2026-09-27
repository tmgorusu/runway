"""Placeholder cost scalar used until Wear's marginal_wear imports.

Any command that uses it prints `cost=stub`. It does not import aging.
"""

from __future__ import annotations

import numpy as np

_announced = False


def announce() -> None:
    global _announced
    if not _announced:
        print("cost=stub", flush=True)
        _announced = True


def stub_cost(u, call, power_kw, dt_s, assumption="nominal") -> float:
    return 1.0 + u.sun_exposure + max(0.0, 1.0 - u.health)


def stub_cost_matrix(units, call, grid_kw: np.ndarray, dt_s: float, assumption="nominal") -> np.ndarray:
    """Vectorized stub over a (units x grid) power grid. Constant in power."""
    sun = np.fromiter((u.sun_exposure for u in units), float, len(units))
    health = np.fromiter((u.health for u in units), float, len(units))
    per_unit = 1.0 + sun + np.maximum(0.0, 1.0 - health)
    return np.broadcast_to(per_unit[:, None], grid_kw.shape).copy()
