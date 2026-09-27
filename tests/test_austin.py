import json
import re
import shutil
import subprocess
from datetime import date

import numpy as np
import pandas as pd
import pytest

from runway import austin, telemetry
from runway.contracts import Call
from runway.fleet import make_fleet
from runway.paths import ROOT


@pytest.fixture(scope="module")
def events():
    return austin.detect_anomalies()


@pytest.fixture(scope="module")
def solved(events):
    """One 4CP + price-spike day solved end to end (zone curves, game, unit-level realization)."""
    reg = pd.read_parquet(telemetry.REGISTRY)
    daily = pd.read_parquet(telemetry.DAILY)
    daily["day"] = pd.to_datetime(daily["day"])
    zone_of, zones = austin.build_zones(reg)
    ev = next(e for e in events if "price_spike" in e["types"] and "4cp_candidate" in e["types"])
    call = Call("t", pd.Timestamp(ev["start_utc"]).to_pydatetime(), 90.0, ev["mw"], "Austin Energy", 0.98, ev["ambient_c"])
    units, stale = austin.event_units(make_fleet(len(reg)), daily, ev)
    curves, ctx = austin.zone_curves(units, zone_of, zones, call)
    F = austin.default_activation_cost(curves, call.kw_requested)
    res = austin.solve(curves, call.kw_requested, F)
    real = austin.realize(units, zone_of, zones, curves, ctx, call, res)
    return {"ev": ev, "zones": zones, "zone_of": zone_of, "curves": curves, "F": F, "res": res, "real": real, "call": call}


def test_anomaly_rule(events):
    from runway.study import CALLS_PATH
    calls = pd.read_parquet(CALLS_PATH)
    call_days = {d.isoformat() for d in calls["start"].dt.tz_convert("America/Chicago").dt.date}
    days = {e["day"] for e in events}
    assert call_days <= days
    for e in events:
        assert date(2025, 4, 1) <= date.fromisoformat(e["day"]) <= date(2025, 9, 30)
        assert e["types"] and set(e["types"]) <= set(austin.MW_BY_TYPE)
        assert e["mw"] == max(austin.MW_BY_TYPE[t] for t in e["types"])
        if "price_spike" in e["types"]:
            assert e["price_max"] >= 100.0 and e["price_max"] >= 3.0 * e["price_trailing_median"]
        assert len(e["price_24h"]) >= 23 and len(e["temp_24h"]) >= 23


def test_zones_cover_the_fleet():
    reg = pd.read_parquet(telemetry.REGISTRY)
    zone_of, zones = austin.build_zones(reg)
    counts = np.bincount(zone_of, minlength=len(zones))
    assert counts.sum() == len(reg) and (counts >= austin.MIN_ZONE_UNITS).all()
    assert all(austin.HOST_RANGE[0] <= z["host_frac"] <= austin.HOST_RANGE[1] for z in zones)
    assert [z["n_units"] for z in zones] == counts.tolist()


def test_equilibrium_clears_within_hosting_limits(solved):
    curves, R = solved["curves"], solved["call"].kw_requested
    ids = list(range(len(curves)))
    eq = austin.equilibrium(curves, ids, R, 1.0)
    assert eq["shortfall"] == 0.0 and abs(sum(eq["dispatch"]) - R) <= 1e-6
    for i, d in zip(ids, eq["dispatch"]):
        assert -1e-9 <= d <= curves[i]["host_kw"] + 1e-6
    for z in curves:
        assert z["lam"] == sorted(z["lam"]) and z["kw"] == sorted(z["kw"]) and z["cost"] == sorted(z["cost"])


def test_coalition_improves_on_calling_everyone(solved):
    curves, R, F = solved["curves"], solved["call"].kw_requested, solved["F"]
    pen = 10 * max(z["lam"][-1] for z in curves)
    eq0 = austin.equilibrium(curves, list(range(len(curves))), R, 1.0)
    start = [i for i, d in enumerate(eq0["dispatch"]) if d > 1e-9]
    res = solved["res"]
    assert res["total_cost"] <= austin.coalition_cost(curves, start, R, F, 1.0, pen)[0] + 1e-15
    assert res["shortfall_kw"] == 0.0 and abs(sum(res["dispatch_kw"]) - R) <= 1e-6


def test_shapley_is_efficient(solved):
    res = solved["res"]
    assert abs(sum(res["shapley"]) - res["coalition_value"]) <= 1e-9 * abs(res["coalition_value"])


def test_outage_zone_is_never_called(solved):
    curves, R, F = solved["curves"], solved["call"].kw_requested, solved["F"]
    out = solved["res"]["members"][:2]
    res = austin.solve(curves, R, F, out=out, M=16)
    assert not set(out) & set(res["members"])


def test_unit_level_dispatch_respects_every_limit(solved):
    g = solved["real"]["game"]
    assert g["reserve_violations"] == 0 and g["feeder_violations"] == 0
    assert abs(g["delivered_kw"] - solved["call"].kw_requested) <= 1e-3
    assert solved["real"]["even"]["feeder_violations"] >= 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_solver_matches_python(solved, tmp_path):
    curves, R, F = solved["curves"], solved["call"].kw_requested, solved["F"]
    (tmp_path / "zones.json").write_text(json.dumps(curves))
    script = (f"const G=require({json.dumps(str(ROOT / 'runway' / 'austin_game.js'))});"
              f"const z=JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'zones.json'))},'utf8'));"
              f"console.log(JSON.stringify(G.solve(z,{{R:{R!r},F:{F!r},hostMul:1,out:[],M:128,seed:7}})));")
    js = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    py = solved["res"]
    assert js["members"] == py["members"]
    assert js["lam"] == pytest.approx(py["lam"], rel=1e-12)
    assert js["dispatch_kw"] == pytest.approx(py["dispatch_kw"], rel=1e-9, abs=1e-9)
    assert js["shapley"] == pytest.approx(py["shapley"], rel=1e-9, abs=1e-15)
    assert js["total_cost"] == pytest.approx(py["total_cost"], rel=1e-12)


def test_page_is_offline_and_labeled():
    page = ROOT / "web" / "austin.html"
    if not page.exists():
        pytest.skip("web/austin.html not built yet")
    html = page.read_text()
    assert "synthetic fleet" in html and "RunwayGame" in html and "{{payload}}" not in html
    assert not re.search(r"<(script|link|img)[^>]+(src|href)=\"https?://", html)
    assert "fetch(" not in html
