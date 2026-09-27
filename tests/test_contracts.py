import dataclasses
import inspect

import pytest

from runway import contracts
from runway.contracts import (
    CAPACITY_KWH, P_MAX_KW, RESERVE_FRAC, REPLACEMENT_HEALTH, Call, ReserveViolation,
    Setpoint, UnitState, check_setpoint, energy_above_reserve_kwh, feasible_kw,
)
from runway.fleet import make_fleet


def test_allocate_signature_matches_vision():
    allocate = pytest.importorskip("runway.allocate").allocate
    params = list(inspect.signature(allocate).parameters)
    assert params == ["call", "units", "policy", "assumption"]
    assert inspect.signature(allocate).parameters["assumption"].default == "nominal"


def test_make_fleet_signature():
    sig = inspect.signature(make_fleet)
    assert list(sig.parameters)[:2] == ["n", "seed"]
    assert sig.parameters["seed"].default == 7


def test_energy_above_reserve_signature():
    assert list(inspect.signature(energy_above_reserve_kwh).parameters) == ["u"]


def test_labeled_assumptions():
    assert RESERVE_FRAC == 0.30 and REPLACEMENT_HEALTH == 0.70
    doc = contracts.__doc__
    assert "RESERVE_FRAC" in doc and "REPLACEMENT_HEALTH" in doc and "assumption" in doc.lower()


def test_energy_above_reserve_full_unit():
    u = UnitState(unit_id=0)
    assert energy_above_reserve_kwh(u) == pytest.approx(0.70 * 39.2)
    # 90 minutes at 20 kW (30 kWh) does not fit above reserve.
    assert feasible_kw(u, 1.5) == pytest.approx(27.44 / 1.5)


def test_guard_rejects_setpoint_entering_reserve():
    u = UnitState(unit_id=0)
    with pytest.raises(ReserveViolation):
        check_setpoint(u, Setpoint(0, 20.0), hours=1.5)
    check_setpoint(u, Setpoint(0, 27.44 / 1.5), hours=1.5)


def test_guard_rejects_power_outside_box():
    u = UnitState(unit_id=0)
    with pytest.raises(ReserveViolation):
        check_setpoint(u, Setpoint(0, -1.0), hours=0.1)
    with pytest.raises(ReserveViolation):
        check_setpoint(u, Setpoint(0, 21.0), hours=0.1)


def test_guard_rejects_unit_at_reserve():
    u = UnitState(unit_id=0, soc=0.30)
    assert energy_above_reserve_kwh(u) == 0.0
    with pytest.raises(ReserveViolation):
        check_setpoint(u, Setpoint(0, 0.5), hours=1.0)


def test_fleet_seed7_deterministic_and_synthetic():
    a, b = make_fleet(200, seed=7), make_fleet(200, seed=7)
    assert [dataclasses.astuple(x) for x in a] == [dataclasses.astuple(x) for x in b]
    assert make_fleet(200, seed=8)[0].sun_exposure != a[0].sun_exposure
    assert all(u.synthetic for u in a)
    assert all(u.p_max_kw == P_MAX_KW and u.capacity_kwh == CAPACITY_KWH for u in a)
    assert {u.term_years for u in a} == {10, 12}
    assert all(0 <= u.age_years <= u.term_years for u in a)
    assert all(0 <= u.sun_exposure <= 1 for u in a)


def test_fleet_health_flag():
    u = make_fleet(4)[0]
    assert "health_initialized" in u.metadata
    if not u.metadata["health_initialized"]:
        assert u.health == 1.0


def test_call_units():
    from datetime import datetime, timezone
    c = Call("c1", datetime(2025, 6, 20, 21, tzinfo=timezone.utc), 90, 40.0)
    assert c.hours == 1.5 and c.kw_requested == 40000.0
