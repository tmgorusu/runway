import pytest

from runway.assumptions import SETS, load
from runway.contracts import UnitState
from runway.thermal import heat_w, step_thermal


def test_zero_power_zero_sun_settles_to_ambient():
    u = UnitState(0, sun_exposure=0.0, enclosure_c=50.0)
    assert abs(step_thermal(u, 35.0, 0.0, 86400.0) - 35.0) <= 0.1


@pytest.mark.parametrize("name", SETS)
def test_steady_temperature_rises_with_power_and_sun(name):
    temps = [step_thermal(UnitState(0, sun_exposure=0.5, enclosure_c=35.0), 35.0, p, 86400.0, name)
             for p in (0, 5, 10, 15, 20)]
    assert all(b > a for a, b in zip(temps, temps[1:]))
    temps = [step_thermal(UnitState(0, sun_exposure=s, enclosure_c=35.0), 35.0, 10.0, 86400.0, name)
             for s in (0, 0.25, 0.5, 0.75, 1.0)]
    assert all(b > a for a, b in zip(temps, temps[1:]))


def test_energy_balance_residual():
    p = load("nominal")
    u = UnitState(0, sun_exposure=1.0, enclosure_c=38.0)
    amb, power, dt = 38.0, 20.0, 5400.0
    t_end = step_thermal(u, amb, power, dt)
    n = 54000
    h = dt / n
    integral, absolute = 0.0, 0.0
    for i in range(n):
        t = step_thermal(u, amb, power, (i + 0.5) * h)
        q = heat_w(p, amb, 1.0, power, t)
        integral += q * h
        absolute += abs(q) * h
    stored = p["thermal_mass_j_per_k"] * (t_end - u.enclosure_c)
    assert abs(stored - integral) / absolute <= 1e-6


def test_one_step_equals_many_steps():
    from dataclasses import replace
    u = UnitState(0, sun_exposure=0.7, enclosure_c=30.0)
    one = step_thermal(u, 40.0, 12.0, 5400.0)
    v = u
    for _ in range(18):
        v = replace(v, enclosure_c=step_thermal(v, 40.0, 12.0, 300.0))
    assert abs(one - v.enclosure_c) <= 1e-9


def test_fahrenheit_ambient_rejected():
    with pytest.raises(ValueError):
        step_thermal(UnitState(0), 95.0, 0.0, 60.0)
