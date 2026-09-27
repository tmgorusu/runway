import ast
import inspect
import json
import textwrap

import pytest

from runway import aging, assumptions, overlay
from runway.assumptions import SETS
from runway.contracts import UnitState
from runway.paths import ASSUMPTIONS

REF = overlay.REFERENCE_CSV


@pytest.mark.skipif(not REF.exists(), reason="blast_reference_missing")
def test_blast_reference_bands():
    result = overlay.build(out_path=overlay.OVERLAY_PATH)
    assert result["status"] == "ok"
    assert result["free_parameters_fit"] == 0
    c = result["checks"]
    assert c["calendar_25c_365d"]["relative_error"] <= 0.02
    assert c["calendar_45c_365d"]["relative_error"] <= 0.02
    assert c["cycle_25c_1000efc"]["relative_error"] <= 0.05
    assert "cycle_45c_ours_over_blast" in result


def test_overlay_marked_failed_without_reference(tmp_path):
    out = overlay.build(path=tmp_path / "missing.csv", out_path=tmp_path / "overlay.json")
    assert out == {"status": "failed", "reason": "blast_reference_missing"}


@pytest.mark.parametrize("name", SETS)
def test_hotter_loses_more(name):
    u = UnitState(0, soc=1.0, age_years=5.0, metadata={"efc": 500.0})
    losses = [aging.age(u, t, 10.0, 5400.0, name) for t in (25.0, 35.0, 45.0)]
    assert losses[2] > losses[1] > losses[0] > 0
    assert aging.age(u, 35.0, 10.0, 5400.0, name) > aging.age(u, 35.0, 0.0, 5400.0, name) > 0


def test_age_body_has_no_hidden_constants():
    src = textwrap.dedent(inspect.getsource(aging.age))
    allowed = {0.0, 1.0, 273.15, 3600.0, 86400.0}
    floats = {n.value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and isinstance(n.value, float)}
    assert floats <= allowed


def test_swapping_assumption_file_changes_loss():
    u = UnitState(0, soc=1.0, age_years=5.0, metadata={"efc": 500.0})
    nom = aging.age(u, 35.0, 10.0, 5400.0, "nominal")
    high = aging.age(u, 35.0, 10.0, 5400.0, "high_sensitivity")
    assert abs(high - nom) / nom >= 0.01


def test_loader_rejects_negative_activation_energy():
    p = json.loads((ASSUMPTIONS / "nominal.json").read_text())
    p["ea_cyc_j_per_mol"] = -1.0
    with pytest.raises(ValueError):
        assumptions.validate(p)


@pytest.mark.parametrize("name", SETS)
def test_cold_charge_does_not_ship(name):
    assert json.loads((ASSUMPTIONS / f"{name}.json").read_text())["cold_charge_enabled"] is False


def test_initial_health_declines_with_age():
    h = [aging.initial_health(a, 10) for a in (0.0, 2.0, 5.0, 10.0)]
    assert h[0] == 1.0 and all(b < a for a, b in zip(h, h[1:]))
