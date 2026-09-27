import json
import subprocess
import sys

import pandas as pd

from runway import ingest


def test_offline_cli_matches_manifest(offline_env):
    r = subprocess.run(
        [sys.executable, "-m", "runway.ingest", "--offline"],
        cwd=ingest.ROOT, env=offline_env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "cache ok" in r.stdout


def test_subprocess_network_really_blocked(offline_env):
    code = "import urllib.request; urllib.request.urlopen('https://www.eia.gov', timeout=5)"
    r = subprocess.run([sys.executable, "-c", code], env=offline_env, capture_output=True, text=True)
    assert r.returncode != 0 and "network disabled" in r.stderr


def test_verify_detects_tampering(tmp_path):
    manifest = json.loads(ingest.MANIFEST.read_text())
    key = "fixture/published_4cp"
    manifest["files"][key]["sha256"] = "0" * 64
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps(manifest))
    assert any(key in p for p in ingest.verify(bad))


def test_fixture_builds_june_published_interval():
    cp = ingest.load("published_4cp", fixture=True)
    assert len(cp) == 1 and cp.iloc[0]["month"] == "June"
    load = ingest.load("ercot_load", fixture=True)
    end = cp.iloc[0]["interval_end_utc"]
    hour = load[(load.ts_utc >= end - pd.Timedelta(hours=1)) & (load.ts_utc <= end + pd.Timedelta(hours=1))]
    assert len(hour) >= 2 and hour.forecast_mw.notna().all()
    weather = ingest.load("weather", fixture=True)
    assert set(weather.city) == {"Austin", "Seguin", "Denton"}


def test_full_cache_covers_summer():
    load = ingest.load("ercot_load")
    local = load.ts_utc.dt.tz_convert(ingest.TZ)
    assert set(local[local.dt.month.isin([6, 7, 8, 9])].dt.month) == {6, 7, 8, 9}
    assert len(ingest.load("published_4cp")) == 4
