import dataclasses
import hashlib
import os
import subprocess
import sys

import pytest

from runway import policies
from runway.contracts import energy_above_reserve_kwh, feasible_kw
from runway.fleet import make_fleet


def fleet32():
    units = make_fleet(32)
    units[3] = dataclasses.replace(units[3], soc=0.5)
    units[5] = dataclasses.replace(units[5], soc=0.31)
    return units


@pytest.mark.parametrize("fn", [policies.even, policies.most_charge])
@pytest.mark.parametrize("mw", [0.1, 0.3, 1.0])
def test_caps_reserve_and_sum(make_call, fn, mw):
    units = fleet32()
    call = make_call(mw)
    sps, short = fn(call, units)
    for u, s in zip(units, sps):
        assert s.unit_id == u.unit_id
        assert 0.0 <= s.power_kw <= 20.0
        assert s.power_kw * call.hours <= energy_above_reserve_kwh(u) + 1e-9
    assert short >= 0
    assert abs(sum(s.power_kw for s in sps) + short - call.kw_requested) <= 1e-6


@pytest.mark.parametrize("fn", [policies.even, policies.most_charge])
def test_over_request_reports_shortfall(make_call, fn):
    units = make_fleet(32)
    call = make_call(1.0)
    caps = sum(feasible_kw(u, call.hours) for u in units)
    _, short = fn(call, units)
    assert abs(short - (1000.0 - caps)) <= 1e-6


def test_most_charge_order(make_call):
    socs = [0.9, 0.95, 0.9, 1.0, 0.6, 0.95, 0.9, 0.8]
    units = [dataclasses.replace(u, soc=s) for u, s in zip(make_fleet(8), socs)]
    call = make_call((feasible_kw(units[3], 1.5) + 1.0) / 1000.0)
    sps, _ = policies.most_charge(call, units)
    p = [s.power_kw for s in sps]
    assert p[3] == pytest.approx(feasible_kw(units[3], 1.5))
    assert p[1] == pytest.approx(1.0) and p[5] == 0.0


SCRIPT = ("from datetime import datetime, timezone; from runway import policies; from runway.contracts import Call; "
          "from runway.fleet import make_fleet; u=make_fleet(64); "
          "c=Call('x', datetime(2025, 8, 1, 21, tzinfo=timezone.utc), 90, 0.9); "
          "print(repr(policies.even(c, u)), repr(policies.most_charge(c, u)))")


def test_identical_across_processes():
    digests = set()
    for seed in ("0", "1"):
        out = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, check=True,
                             env={**os.environ, "PYTHONHASHSEED": seed}).stdout
        digests.add(hashlib.sha256(out.encode()).hexdigest())
    assert len(digests) == 1
