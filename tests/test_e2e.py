import json
import subprocess
import sys

from runway import ingest


def test_quarter_milestone_offline(offline_env):
    r = subprocess.run([sys.executable, "-m", "runway.e2e", "--offline"], cwd=ingest.ROOT,
                       env=offline_env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads((ingest.ROOT / "outputs/m1/dispatch.json").read_text())
    assert out["synthetic"] is True and len(out["setpoints"]) == 32
    assert out["call"]["call_id"] == "published-4cp-2025-06-19"
    assert out["shortfall_kw"] >= 0
    assert abs(out["delivered_kw"] + out["shortfall_kw"] - out["call"]["mw_requested"] * 1000) < 1e-3
    assert r.stdout.count("call_id=") == 1
    if out["cost"] == "stub":
        assert "cost=stub" in r.stdout
