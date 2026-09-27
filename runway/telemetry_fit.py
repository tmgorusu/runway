"""Estimate each unit's state from the synthetic Base-like telemetry, as of the replay start.

Uses only what telemetry would report, from 2025-04-01 up to 2025-06-01 (no future data):
  sun_exposure  median daily enclosure excess over Austin's daily mean, on non-dispatch days with
                at least 20 heartbeats, divided by the excess the nominal thermal model gives at
                sun 1.0 (solar gain * daily-mean fraction / conductance), clipped to [0, 1]
  health        the unit's last BMS state-of-health reading, as a fraction
  efc           lifetime throughput counter plus window throughput, / (2 * 39.2 kWh)
  age_years     from the install date
  last_seen     the unit's last heartbeat before the snapshot

Writes outputs/telemetry/fleet_estimates.parquet (read by runway.fleet) and scores the
estimates against the hidden truth in outputs/physics/telemetry_fit.json. Pass bands were
fixed before the first fit: exposure correlation >= 0.95 and MAE <= 0.05; health MAE <= 0.006.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from runway import telemetry
from runway.assumptions import load
from runway.contracts import CAPACITY_KWH
from runway.paths import OUTPUTS, write_json

ESTIMATES = OUTPUTS / "telemetry" / "fleet_estimates.parquet"
FIT_REPORT = OUTPUTS / "physics" / "telemetry_fit.json"
MIN_HEARTBEATS = 20
BANDS = {"exposure_corr_min": 0.95, "exposure_mae_max": 0.05, "health_mae_max": 0.006}


def _austin_daily_mean() -> pd.Series:
    from runway import ingest
    w = ingest.load("weather")
    w = w[w["city"] == "Austin"].copy()
    w["day"] = w["ts_utc"].dt.tz_convert(telemetry.TZ).dt.date
    return w.groupby("day")["temp_c"].mean()


def estimate() -> pd.DataFrame:
    p = load("nominal")
    reg = pd.read_parquet(telemetry.REGISTRY)
    daily = pd.read_parquet(telemetry.DAILY)
    snap = pd.Timestamp(telemetry.SNAPSHOT_UTC)
    window = daily[daily["day"] < snap.tz_convert(telemetry.TZ).tz_localize(None).normalize()]
    amb = _austin_daily_mean()
    window = window.assign(ambient=window["day"].dt.date.map(amb))
    quiet = window[(window["heartbeats"] >= MIN_HEARTBEATS) & (window["discharged_kwh"] == 0)]
    excess = (quiet["enclosure_mean_c"] - quiet["ambient"]).groupby(quiet["unit_id"]).median()
    per_sun = p["solar_gain_w_at_sun1"] * p["solar_daily_mean_fraction"] / p["ua_w_per_k"]
    sun = (excess / per_sun).clip(0.0, 1.0).reindex(reg["unit_id"]).fillna(0.5)
    last = window.dropna(subset=["soh_pct"]).sort_values("day").groupby("unit_id").tail(1).set_index("unit_id")
    health = (last["soh_pct"] / 100.0).reindex(reg["unit_id"])
    throughput = window.groupby("unit_id")[["discharged_kwh", "charged_kwh"]].sum().reindex(reg["unit_id"]).fillna(0.0)
    efc = (reg.set_index("unit_id")["lifetime_kwh_before_window"] + throughput.sum(axis=1) / 2.0) / CAPACITY_KWH
    age = (snap - reg.set_index("unit_id")["install_date"]).dt.total_seconds() / (365.25 * 86400.0)
    last_seen = window.groupby("unit_id")["last_seen_utc"].max().reindex(reg["unit_id"])
    return pd.DataFrame({
        "unit_id": reg["unit_id"].to_numpy(),
        "term_years": reg["term_years"].to_numpy(),
        "mount": reg["mount"].to_numpy(),
        "age_years": age.to_numpy(),
        "sun_exposure": sun.to_numpy(),
        "health": health.to_numpy(),
        "efc": efc.to_numpy(),
        "last_seen_utc": last_seen.to_numpy(),
        "quiet_days": quiet.groupby("unit_id").size().reindex(reg["unit_id"]).fillna(0).astype(int).to_numpy(),
        "synthetic": True,
    })


def score(est: pd.DataFrame) -> dict:
    truth = pd.read_parquet(telemetry.TRUTH).set_index("unit_id").loc[est["unit_id"]]
    exp_err = est["sun_exposure"].to_numpy() - truth["effective_exposure"].to_numpy()
    h_err = est["health"].to_numpy() - truth["health_at_snapshot"].to_numpy()
    e_err = est["efc"].to_numpy() - truth["efc_at_snapshot"].to_numpy()
    out = {
        "synthetic": True,
        "note": "Estimates from synthetic Base-like telemetry scored against the hidden parameters that generated it.",
        "window": "2025-04-01 to 2025-05-31 (before the replay start)",
        "exposure_corr": float(np.corrcoef(est["sun_exposure"], truth["effective_exposure"])[0, 1]),
        "exposure_mae": float(np.mean(np.abs(exp_err))),
        "exposure_corr_with_raw_sun": float(np.corrcoef(est["sun_exposure"], truth["sun_exposure"])[0, 1]),
        "health_mae": float(np.nanmean(np.abs(h_err))),
        "efc_median_rel_error": float(np.median(np.abs(e_err) / np.maximum(truth["efc_at_snapshot"].to_numpy(), 1.0))),
        "units_with_outage": int((truth["outage_days"] > 0).sum()),
        "bands": BANDS,
    }
    out["pass"] = (out["exposure_corr"] >= BANDS["exposure_corr_min"] and out["exposure_mae"] <= BANDS["exposure_mae_max"]
                   and out["health_mae"] <= BANDS["health_mae_max"])
    return out


def run() -> dict:
    if telemetry.verify():
        telemetry.write()
    est = estimate()
    ESTIMATES.parent.mkdir(parents=True, exist_ok=True)
    est.to_parquet(ESTIMATES, index=False)
    report = score(est)
    write_json(FIT_REPORT, report)
    return report


if __name__ == "__main__":
    r = run()
    print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k not in ("bands", "note")})
    sys.exit(0)
