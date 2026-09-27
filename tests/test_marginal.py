import math
import statistics
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from runway import allocate as alloc
from runway.aging import age
from runway.assumptions import SETS, load
from runway.contracts import Call, UnitState
from runway.fleet import make_fleet
from runway.marginal import GL3_W, marginal_grid, marginal_wear, node_times, unit_arrays
from runway.thermal import steady_state_c, step_thermal

START = datetime(2025, 8, 1, 21, 15, tzinfo=timezone.utc)


def call(ambient=38.0, odds=0.98):
    return Call("t", START, 90.0, 40.0, "Austin Energy", odds, ambient)


def rebuilt(u, c, power, name="nominal"):
    """W(P+h) - W(P) from public age and step_thermal only."""
    p = load(name)
    u0 = replace(u, enclosure_c=steady_state_c(c.ambient_c, u.sun_exposure, 0.0, name))

    def W(P):
        return sum(0.5 * w * (age(u0, step_thermal(u0, c.ambient_c, P, t, name), P, (c.duration_min * 60.0), name)
                              - age(u0, step_thermal(u0, c.ambient_c, 0.0, t, name), 0.0, (c.duration_min * 60.0), name))
                   for t, w in zip(node_times((c.duration_min * 60.0)), GL3_W))
    h = p["fd_step_kw"]
    target = 1.0 - 0.30 * u.age_years / u.term_years
    return (W(power + h) - W(power)) / h * math.exp(p["glide_k"] * (target - u.health))


def test_scalar_is_finite_difference_of_age_and_step_thermal():
    for u in (replace(v, last_seen=None) for v in make_fleet(8)):
        for P in (0.0, 7.5, 16.0):
            m = marginal_wear(u, call(), P, 5400.0)
            assert abs(m - rebuilt(u, call(), P)) / m <= 1e-9


@pytest.mark.parametrize("name", SETS)
def test_monotone_nondecreasing_in_power(name):
    units = make_fleet(64)
    arr = unit_arrays(units)
    grid = np.tile(np.linspace(0.0, 20.0, 401), (64, 1))
    for amb in (22.0, 38.0, 44.0):
        m = marginal_grid(arr, call(amb), grid, 5400.0, name)
        assert np.all(np.diff(m, axis=1) >= 0.0)
        assert np.all(m[:, 0] > 0)


def test_grid_matches_scalar():
    units = make_fleet(16)
    arr = unit_arrays(units)
    P = np.tile(np.array([0.0, 3.0, 11.0, 19.0]), (16, 1))
    g = marginal_grid(arr, call(), P, 5400.0)
    for i, u in enumerate(units):
        for j, p in enumerate(P[i]):
            s = marginal_wear(u, call(), p, 5400.0)
            assert abs(g[i, j] - s) / s <= 1e-9


@pytest.mark.parametrize("name,floor", [("nominal", 1.10), ("high_sensitivity", 1.10), ("low_sensitivity", 1.0)])
def test_sun_costs_more(name, floor):
    base = UnitState(0, age_years=5.0, term_years=10, health=0.94, metadata={"efc": 500.0})
    shade = marginal_wear(replace(base, sun_exposure=0.0), call(), 10.0, 5400.0, name)
    sun = marginal_wear(replace(base, sun_exposure=1.0), call(), 10.0, 5400.0, name)
    assert sun / shade >= floor


@pytest.mark.parametrize("name", ["nominal+glide_k=10", "high_sensitivity+glide_k=10"])
def test_behind_glide_path_costs_more(name):
    k = load(name)["glide_k"]
    on_path = UnitState(0, age_years=5.0, term_years=10, health=0.85, metadata={"efc": 500.0})
    behind = replace(on_path, health=0.80)
    ratio = marginal_wear(behind, call(), 10.0, 5400.0, name) / marginal_wear(on_path, call(), 10.0, 5400.0, name)
    assert abs(ratio - math.exp(0.05 * k)) <= 1e-12 * math.exp(0.05 * k)
    if name.startswith("nominal"):
        assert abs(ratio - 1.6487212707) < 1e-9


def test_nominal_glide_is_off():
    assert all(load(s)["glide_k"] == 0.0 for s in SETS)
    assert load("nominal")["glide_k_sensitivity"] == 10.0
    on_path = UnitState(0, age_years=5.0, term_years=10, health=0.85, metadata={"efc": 500.0})
    behind = replace(on_path, health=0.80)
    assert marginal_wear(behind, call(), 10.0, 5400.0) == marginal_wear(on_path, call(), 10.0, 5400.0)


def test_stale_telemetry_doubles_at_high_peak_odds():
    u = UnitState(0, age_years=5.0, health=0.9, metadata={"efc": 500.0})
    fresh = marginal_wear(replace(u, last_seen=START - timedelta(seconds=899)), call(), 10.0, 5400.0)
    stale = marginal_wear(replace(u, last_seen=START - timedelta(seconds=901)), call(odds=0.9), 10.0, 5400.0)
    assert stale == pytest.approx(2.0 * fresh, rel=1e-12)
    with pytest.raises(TypeError):
        marginal_wear(replace(u, last_seen=datetime(2025, 8, 1)), call(), 10.0, 5400.0)


def test_quadrature_error_bound():
    from runway.marginal import call_wear
    arr = {"sun": np.array([1.0]), "soc": np.array([1.0]), "age_years": np.array([5.0]), "efc": np.array([500.0])}
    gl = float(call_wear(arr, 44.0, np.array([20.0]), 5400.0)[0])
    p = load("nominal")
    u = UnitState(0, sun_exposure=1.0, soc=1.0, age_years=5.0, metadata={"efc": 500.0},
                  enclosure_c=steady_state_c(44.0, 1.0, 0.0))
    n = 5400
    fine = sum(age(u, step_thermal(u, 44.0, 20.0, (i + 0.5)), 20.0, 5400.0) - age(u, step_thermal(u, 44.0, 0.0, i + 0.5), 0.0, 5400.0)
               for i in range(n)) / n
    assert abs(gl - fine) / fine <= 1e-5


def test_scalar_speed():
    u = make_fleet(1)[0]
    c = call()
    times = []
    for _ in range(200):
        t = time.perf_counter()
        marginal_wear(u, c, 10.0, 5400.0)
        times.append(time.perf_counter() - t)
    p50_us = statistics.median(times) * 1e6
    print(f"marginal_wear scalar p50 {p50_us:.1f} us")
    grid_t = time.perf_counter()
    arr = unit_arrays(make_fleet(4000))
    marginal_grid(arr, c, np.tile(np.linspace(0, 18, 20), (4000, 1)), 5400.0)
    per_eval = (time.perf_counter() - grid_t) / (4000 * 20) * 1e6
    print(f"vectorized path {per_eval:.2f} us per unit-point")
    assert per_eval <= 15.0


def test_slsqp_gap_on_16_units():
    from scipy.optimize import minimize
    from runway.marginal import call_wear, weights
    units = [replace(u, last_seen=None) for u in make_fleet(16)]
    c = replace(call(), mw_requested=0.16)
    arr = unit_arrays(units)
    caps = alloc.feasible_caps(units, c.hours)
    w = weights(arr, c)

    def objective(P):
        return float(np.sum(w * call_wear(arr, c.ambient_c, np.asarray(P), (c.duration_min * 60.0))))

    setpoints, short = alloc.allocate(c, units, "runway")
    ours = objective([s.power_kw for s in setpoints])
    res = minimize(objective, np.full(16, c.kw_requested / 16), method="SLSQP",
                   bounds=[(0, float(cap)) for cap in caps],
                   constraints=[{"type": "eq", "fun": lambda P: np.sum(P) - c.kw_requested}],
                   options={"ftol": 1e-16, "maxiter": 500})
    gap = (ours - res.fun) / res.fun
    print(f"SLSQP gap {100 * gap:.4f}%")
    assert short == 0.0
    assert gap <= 0.01
