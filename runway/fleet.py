"""Deterministic synthetic fleet. Seed 7 is the demo fleet. Nothing here is Base telemetry.

Assumptions: half the units on 10-year terms, half on 12-year; ages uniform across
each unit's term; sun exposure uniform on [0, 1]; SOC 1.0 at the start of a call.
"""

from __future__ import annotations

import numpy as np

from runway.contracts import EPOCH, UnitState

try:  # Wear owns aging; the fleet still builds before it lands.
    from runway.aging import initial_health as _initial_health
except ImportError:  # pragma: no cover - depends on Wear's progress
    _initial_health = None


def make_fleet(n: int, seed: int = 7, assumption: str = "nominal") -> list[UnitState]:
    """Deterministic synthetic fleet. 20 kW, 39.2 kWh.

    Calls Wear.initial_health when that function imports.
    Otherwise health is 1.0 and metadata health_initialized is false.
    """
    rng = np.random.default_rng(seed)
    terms = np.where(np.arange(n) % 2 == 0, 10, 12)
    ages = rng.uniform(0.0, 1.0, n) * terms
    sun = rng.uniform(0.0, 1.0, n)
    units = []
    for i in range(n):
        term, age = int(terms[i]), float(ages[i])
        if _initial_health is not None:
            health, initialized = float(_initial_health(age, term, assumption)), True
        else:
            health, initialized = 1.0, False
        units.append(
            UnitState(
                unit_id=i,
                soc=1.0,
                health=health,
                age_years=age,
                term_years=term,
                sun_exposure=float(sun[i]),
                last_seen=EPOCH,
                metadata={"seed": seed, "health_initialized": initialized},
            )
        )
    return units
