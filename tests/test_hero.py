import ast
import inspect

from runway import hero
from runway.paths import rule_hash, sha256_file

KEYS = ["synthetic", "fleet_n", "seed", "policy_for_ratio", "rule_hash", "soc_each_call", "assumption_sets",
        "total_capacity_fraction_lost"]
BLOCK = ["wear_per_kwh_miss", "wear_per_kwh_hit", "ratio", "wear_fraction_on_misses", "kwh_miss", "kwh_hit"]


def test_schema_and_identity(tmp_path):
    a = hero.run(fixture=True, out_path=tmp_path / "a.json", n=400)
    hero.run(fixture=True, out_path=tmp_path / "b.json", n=400)
    assert sha256_file(tmp_path / "a.json") == sha256_file(tmp_path / "b.json")
    assert list(a) == KEYS
    assert a["policy_for_ratio"] == "even" and a["soc_each_call"] == 1.0 and a["synthetic"] is True
    assert a["rule_hash"] == ""
    assert set(a["assumption_sets"]) == {"nominal", "low_sensitivity", "high_sensitivity"}
    for b in a["assumption_sets"].values():
        assert list(b) == BLOCK
        assert abs(b["ratio"] - b["wear_per_kwh_miss"] / b["wear_per_kwh_hit"]) <= 1e-12 * b["ratio"]
        wm, wh = b["wear_per_kwh_miss"] * b["kwh_miss"], b["wear_per_kwh_hit"] * b["kwh_hit"]
        assert 0.0 <= b["wear_fraction_on_misses"] <= 1.0
        assert abs(b["wear_fraction_on_misses"] - wm / (wm + wh)) <= 1e-12
    assert set(a["total_capacity_fraction_lost"]) == {"runway", "even", "most_charge"}


def test_real_calls_carry_rule_hash(tmp_path):
    from runway import calendar
    if not calendar.CALLS.exists():
        calendar.run()
    out = hero.run(out_path=tmp_path / "h.json", n=200)
    assert out["rule_hash"] == rule_hash()


def test_no_pass_band_on_ratio():
    tree = ast.parse(inspect.getsource(hero))
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            names = {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            assert "ratio" not in names
