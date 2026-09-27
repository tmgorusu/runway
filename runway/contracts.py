"""Shared contract between Machine and Wear. See VISION.md section 3.

Hardware is Base Core: 20 kW, 39.2 kWh, LFP. Every fleet built in this repo is
synthetic; there is no Base telemetry here.

Labeled assumptions (ours, not from the brief):
- RESERVE_FRAC = 0.30: backup reserve fraction of usable capacity, never dispatched.
- REPLACEMENT_HEALTH = 0.70: a unit below this remaining-capacity fraction is replaced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

P_MAX_KW = 20.0
CAPACITY_KWH = 39.2
RESERVE_FRAC = 0.30  # assumption
REPLACEMENT_HEALTH = 0.70  # assumption
POLICIES = ("runway", "even", "most_charge")
TOL_KW = 1e-6

EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)


@dataclass(slots=True)
class UnitState:
    """One home battery. `health` is remaining capacity fraction (1.0 = new)."""

    unit_id: int
    soc: float = 1.0
    health: float = 1.0
    age_years: float = 0.0
    term_years: int = 10
    sun_exposure: float = 0.0
    enclosure_c: float = 25.0
    last_seen: datetime = EPOCH
    p_max_kw: float = P_MAX_KW
    capacity_kwh: float = CAPACITY_KWH
    reserve_frac: float = RESERVE_FRAC
    online: bool = True
    derate: float = 1.0
    synthetic: bool = True
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Call:
    """A utility megawatt call. `start` is timezone-aware UTC."""

    call_id: str
    start: datetime
    duration_min: float
    mw_requested: float
    utility: str = "Austin Energy"
    peak_odds: float = 0.0
    ambient_c: float = 35.0

    @property
    def hours(self) -> float:
        return self.duration_min / 60.0

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_min)

    @property
    def kw_requested(self) -> float:
        return self.mw_requested * 1000.0


@dataclass(frozen=True, slots=True)
class Setpoint:
    unit_id: int
    power_kw: float
    call_id: str = ""
    seq: int = 0


class ReserveViolation(ValueError):
    """A setpoint would draw energy from backup reserve or exceed power limits."""


def energy_above_reserve_kwh(u: UnitState) -> float:
    """Energy that may be dispatched without touching backup reserve."""
    usable = u.capacity_kwh * u.health
    return max(0.0, (u.soc - u.reserve_frac) * usable)


def feasible_kw(u: UnitState, hours: float) -> float:
    """Largest constant power the unit can hold for `hours` without entering reserve."""
    if not u.online or hours <= 0:
        return 0.0
    return max(0.0, min(u.p_max_kw * u.derate, energy_above_reserve_kwh(u) / hours))


def check_setpoint(u: UnitState, sp: Setpoint, hours: float) -> None:
    """Raise ReserveViolation unless the setpoint is legal for this unit."""
    if sp.unit_id != u.unit_id:
        raise ValueError(f"setpoint for unit {sp.unit_id} sent to unit {u.unit_id}")
    if sp.power_kw < -TOL_KW or sp.power_kw > u.p_max_kw * u.derate + TOL_KW:
        raise ReserveViolation(f"unit {u.unit_id}: {sp.power_kw} kW outside [0, {u.p_max_kw * u.derate}]")
    if sp.power_kw * hours > energy_above_reserve_kwh(u) + TOL_KW:
        raise ReserveViolation(
            f"unit {u.unit_id}: {sp.power_kw * hours:.4f} kWh exceeds "
            f"{energy_above_reserve_kwh(u):.4f} kWh above reserve"
        )
