"""Deterministic synthetic fleet. Seed 7 is the demo fleet. Nothing here is Base telemetry.

Seed 7 is built from Wear's estimates of synthetic Base-like telemetry
(outputs/telemetry/fleet_estimates.parquet, from runway.telemetry and
runway.telemetry_fit): age from install date, sun exposure fitted from enclosure
temperature, health from the BMS state-of-health reading, cycles from the
throughput counter, last heartbeat. Other seeds, or a checkout without the
estimates, fall back to the assumptions below.

Fallback assumptions: half the units on 10-year terms, half on 12-year; ages uniform
across each unit's term; sun exposure uniform on [0, 1]; SOC 1.0 at the start of a call.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from runway.contracts import EPOCH, UnitState

try:  # Wear owns aging; the fleet still builds before it lands.
    from runway.aging import initial_health as _initial_health
except ImportError:  # pragma: no cover - depends on Wear's progress
    _initial_health = None

TELEMETRY_SEED = 7
ESTIMATES = Path(__file__).resolve().parent.parent / "outputs" / "telemetry" / "fleet_estimates.parquet"
_cache: dict = {}


def _estimates():
    if "est" not in _cache:
        if not ESTIMATES.exists():
            _cache["est"] = None
        else:
            import pandas as pd
            _cache["est"] = pd.read_parquet(ESTIMATES)
    return _cache["est"]


def _from_telemetry(n: int) -> list[UnitState] | None:
    est = _estimates()
    if est is None or n > len(est):
        return None
    units = []
    for r in est.head(n).itertuples(index=False):
        last = r.last_seen_utc.to_pydatetime() if r.last_seen_utc == r.last_seen_utc else EPOCH
        units.append(UnitState(
            unit_id=int(r.unit_id),
            soc=1.0,
            health=float(r.health),
            age_years=float(r.age_years),
            term_years=int(r.term_years),
            sun_exposure=float(r.sun_exposure),
            last_seen=last,
            metadata={"seed": TELEMETRY_SEED, "health_initialized": True, "source": "synthetic_telemetry",
                      "efc": float(r.efc), "mount": r.mount},
        ))
    return units


def make_fleet(n: int, seed: int = 7, assumption: str = "nominal") -> list[UnitState]:
    """Deterministic synthetic fleet. 20 kW, 39.2 kWh.

    Seed 7 comes from synthetic telemetry estimates when they exist.
    Otherwise calls Wear.initial_health when that function imports;
    failing that, health is 1.0 and metadata health_initialized is false.
    """
    if seed == TELEMETRY_SEED and assumption == "nominal":
        units = _from_telemetry(n)
        if units is not None:
            return units
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
                metadata={"seed": seed, "health_initialized": initialized, "source": "assumed_distributions"},
            )
        )
    return units
