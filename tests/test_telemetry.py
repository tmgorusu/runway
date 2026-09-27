import pandas as pd
import pytest

from runway import fleet, telemetry, telemetry_fit

REGISTRY_COLUMNS = ["unit_id", "install_date", "term_years", "zip", "lat", "lon", "mount", "shade_survey", "nameplate_kw",
                    "capacity_kwh", "firmware", "lifetime_kwh_before_window", "synthetic"]
DAILY_COLUMNS = ["unit_id", "day", "heartbeats", "enclosure_mean_c", "enclosure_max_c", "soc_min", "discharged_kwh",
                 "charged_kwh", "soh_pct", "last_seen_utc"]


@pytest.fixture(scope="module")
def cache():
    if telemetry.verify():
        telemetry.write()
    return pd.read_parquet(telemetry.REGISTRY), pd.read_parquet(telemetry.DAILY)


def test_schema_and_labels(cache):
    reg, daily = cache
    assert list(reg.columns) == REGISTRY_COLUMNS and list(daily.columns) == DAILY_COLUMNS
    assert reg["synthetic"].all() and len(reg) == telemetry.N_UNITS
    assert set(reg["term_years"]) == {10, 12}
    assert daily["heartbeats"].between(0, 24).all()
    assert (daily["soh_pct"].dropna() <= 100.0).all()
    assert daily["last_seen_utc"].dropna().min() >= pd.Timestamp("2025-04-01", tz="America/Chicago")
    import json
    assert json.loads(telemetry.MANIFEST.read_text())["synthetic"] is True


def test_telemetry_follows_real_call_days(cache):
    _, daily = cache
    from runway.study import CALLS_PATH
    call_days = set(pd.read_parquet(CALLS_PATH)["start"].dt.tz_convert("America/Chicago").dt.date)
    dispatch_days = set(daily.loc[daily["discharged_kwh"] > 0, "day"].dt.date)
    assert dispatch_days and dispatch_days <= call_days


def test_estimates_never_read_truth():
    src = open(telemetry_fit.__file__).read()
    estimate_src = src[src.index("def estimate"):src.index("def score")]
    assert "TRUTH" not in estimate_src and "truth" not in estimate_src


def test_fit_meets_preregistered_bands():
    """Bands fixed before the first fit: exposure corr >= 0.95, MAE <= 0.05; health MAE <= 0.006."""
    r = telemetry_fit.run()
    assert r["exposure_corr"] >= 0.95 and r["exposure_mae"] <= 0.05
    assert r["health_mae"] <= 0.006
    assert r["pass"] is True


def test_seed7_fleet_comes_from_telemetry():
    units = fleet.make_fleet(200)
    assert all(u.metadata["source"] == "synthetic_telemetry" for u in units)
    assert all(0.0 <= u.sun_exposure <= 1.0 and 0.0 < u.health <= 1.0 for u in units)
    assert all("efc" in u.metadata for u in units)
    assert fleet.make_fleet(200, seed=8)[0].metadata["source"] == "assumed_distributions"
