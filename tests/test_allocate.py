import dataclasses

import numpy as np
import pytest

from runway.allocate import GRID_POINTS, allocate, water_fill
from runway.contracts import TOL_KW, check_setpoint, energy_above_reserve_kwh, feasible_kw
from runway.fleet import make_fleet

FEASIBLE_32 = 32 * 27.44 / 1.5  # kW a full 32-unit fleet can hold for 90 min


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
    units = make_fleet(32)
    sps, _ = allocate(make_call(0.1), units, "runway")
    sun = np.array([u.sun_exposure + max(0, 1 - u.health) for u in units])
    p = np.array([s.power_kw for s in sps])
    # Anything used is no more expensive than anything left idle.
    assert sun[p > 0].max() <= sun[p == 0].min() + 1e-12


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
