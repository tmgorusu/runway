import dataclasses

import numpy as np
import pytest

from runway.allocate import GRID_POINTS, allocate, water_fill
from runway.contracts import TOL_KW, check_setpoint, energy_above_reserve_kwh, feasible_kw
from runway.fleet import make_fleet

# kW the 32-unit seed-7 fleet can hold for 90 min at SOC 1.0, with health from Wear.initial_health when it imports
FEASIBLE_32 = sum(feasible_kw(u, 1.5) for u in make_fleet(32))


def _check(call, units, sps, shortfall):
    by_id = {u.unit_id: u for u in units}
    assert shortfall >= 0
    assert sum(s.power_kw for s in sps) + shortfall == pytest.approx(call.kw_requested, abs=TOL_KW)
    for s in sps:
        u = by_id[s.unit_id]
        assert -TOL_KW <= s.power_kw <= u.p_max_kw + TOL_KW
        assert s.power_kw * call.hours <= energy_above_reserve_kwh(u) + TOL_KW
        check_setpoint(u, s, call.hours)


@pytest.mark.parametrize("mw", [0.0, 0.05, 0.2, 0.4, FEASIBLE_32 / 1000])
def test_runway_box_and_identity(make_call, mw):
    units = make_fleet(32)
    call = make_call(mw)
    sps, short = allocate(call, units, "runway")
    _check(call, units, sps, short)
    assert short == pytest.approx(0.0, abs=1e-6)


def test_infeasible_fleet_reports_shortfall(make_call):
    units = make_fleet(32)
    call = make_call(0.64)  # 32 x 20 kW nameplate, but 90 min does not fit above reserve
    sps, short = allocate(call, units, "runway")
    _check(call, units, sps, short)
    assert short == pytest.approx(640.0 - FEASIBLE_32, rel=1e-9)
    assert all(s.power_kw == pytest.approx(feasible_kw(u, 1.5)) for s, u in zip(sps, units))


def test_offline_units_get_zero(make_call):
    units = make_fleet(32)
    for u in units[:5]:
        u.online = False
    sps, short = allocate(make_call(0.3), units, "runway")
    assert all(s.power_kw == 0.0 for s in sps[:5])
    _check(make_call(0.3), units, sps, short)


def test_units_at_reserve_get_zero(make_call):
    units = make_fleet(32)
    units[3].soc = 0.30
    sps, short = allocate(make_call(0.3), units, "runway")
    assert sps[3].power_kw == 0.0


def test_deterministic(make_call):
    a = allocate(make_call(0.3), make_fleet(500), "runway")
    b = allocate(make_call(0.3), make_fleet(500), "runway")
    assert [dataclasses.astuple(s) for s in a[0]] == [dataclasses.astuple(s) for s in b[0]]
    assert a[1] == b[1]


def test_cheaper_units_fill_first(make_call):
    """Water-fill property for whichever cost is active: every grid point a unit reached costs no more
    than the first grid point of any unit left idle."""
    from runway.allocate import cost_matrix, feasible_caps

    units = make_fleet(32)
    call = make_call(0.1)
    sps, _ = allocate(call, units, "runway")
    caps = feasible_caps(units, call.hours)
    grid = caps[:, None] * np.linspace(0.0, 1.0, GRID_POINTS)[None, :]
    costs = cost_matrix(call, units, grid, "nominal")
    p = np.array([s.power_kw for s in sps])
    reached = [costs[i, grid[i] <= p[i] + 1e-9].max() for i in range(len(units)) if p[i] > 0]
    idle_first = [costs[i, 1] for i in range(len(units)) if p[i] == 0]
    assert reached and idle_first
    assert max(reached) <= min(idle_first) + 1e-12


def test_water_fill_monotone_cost():
    caps = np.array([10.0, 10.0])
    grid = caps[:, None] * np.linspace(0, 1, GRID_POINTS)[None, :]
    costs = np.stack([grid[0] * 1.0, grid[1] * 2.0])  # unit 1 costs twice as much per kW
    p, short = water_fill(12.0, caps, grid, costs)
    assert short == 0.0 and p.sum() == pytest.approx(12.0)
    assert p[0] >= p[1]


def test_unknown_policy(make_call):
    with pytest.raises(ValueError):
        allocate(make_call(0.1), make_fleet(4), "cheapest")


@pytest.mark.parametrize("policy", ["even", "most_charge"])
def test_baselines_match_policies(make_call, policy):
    policies = pytest.importorskip("runway.policies")
    units = make_fleet(32)
    call = make_call(0.3)
    sps, short = allocate(call, units, policy)
    ref_sps, ref_short = getattr(policies, policy)(call, units)
    assert np.allclose([s.power_kw for s in sps], [s.power_kw for s in ref_sps], atol=1e-6)
    assert short == pytest.approx(ref_short, abs=1e-6)
    _check(call, units, sps, short)
