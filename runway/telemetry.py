"""Synthetic stand-in for Base fleet telemetry. Not Base data.

What a Base-like fleet would plausibly report, generated for the 4,000-unit seed-7
synthetic fleet from 2025-04-01 through 2025-09-30:

  data/telemetry/registry.parquet   one row per unit: install date, contract term, ZIP,
                                    lat/lon, mounting, installer shade survey, nameplate,
                                    firmware, lifetime throughput before the window
  data/telemetry/daily.parquet      one row per unit-day: heartbeats received, enclosure
                                    mean/max °C, minimum SOC, discharged and charged kWh,
                                    BMS state-of-health estimate, last heartbeat time
  data/telemetry/truth.parquet      hidden per-unit parameters used to generate the above.
                                    Not observable; read only by the validation in
                                    runway.telemetry_fit
  data/telemetry/manifest.json      sha256 of each file

Drivers are real: Austin hourly temperature from the weather cache and the 2025
candidate call days from handoff/calls.parquet. Everything about the units is an
assumption. The enclosure and aging physics are Wear's own models with per-unit
parameter scatter and sensor noise, so fitting this telemetry tests whether the
estimates recover the parameters; it does not validate the physics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from runway.aging import age_arrays, initial_health, k_cal, power_increment
from runway.assumptions import load
from runway.contracts import CAPACITY_KWH
from runway.paths import DATA

TELEMETRY = DATA / "telemetry"
REGISTRY = TELEMETRY / "registry.parquet"
DAILY = TELEMETRY / "daily.parquet"
TRUTH = TELEMETRY / "truth.parquet"
MANIFEST = TELEMETRY / "manifest.json"

N_UNITS = 4000
SEED = 7
TZ = "America/Chicago"
WINDOW_START = pd.Timestamp("2025-04-01", tz=TZ)
WINDOW_END = pd.Timestamp("2025-10-01", tz=TZ)
SNAPSHOT_UTC = datetime(2025, 6, 1, 5, 0, tzinfo=timezone.utc)

# Assumptions about a Base-like fleet (labeled; nothing here is Base data).
MOUNTS = ("garage_interior", "exterior_north", "exterior_east", "exterior_south", "exterior_west")
MOUNT_P = (0.25, 0.15, 0.20, 0.20, 0.20)
MOUNT_SUN = {"garage_interior": 0.10, "exterior_north": 0.35, "exterior_east": 0.70, "exterior_south": 0.85, "exterior_west": 1.0}
MOUNT_PEAK_H = {"garage_interior": 13.0, "exterior_north": 13.0, "exterior_east": 10.0, "exterior_south": 13.0, "exterior_west": 16.5}
GARAGE_OFFSET_C = 1.0
AUSTIN_ZIPS = ("78701", "78702", "78704", "78721", "78723", "78727", "78731", "78741", "78745", "78748", "78749", "78753")
FIRMWARE = ("3.4.1", "3.5.0", "3.5.2")
EVEN_SHARE_KW = 10.0          # historical dispatch assumed even spread, 40 MW / 4,000 units
RECHARGE_KW = 5.0
TEMP_NOISE_C = 0.3
SOH_NOISE = 0.004
SOH_BIAS_SD = 0.003
USAGE_SPREAD = 0.3            # lognormal sigma on each unit's historical cycling rate


def _hours() -> pd.DatetimeIndex:
    return pd.date_range(WINDOW_START, WINDOW_END, freq="h", inclusive="left").tz_convert("UTC")


def _austin_temps(hours: pd.DatetimeIndex) -> np.ndarray:
    from runway import ingest
    w = ingest.load("weather")
    s = w[w["city"] == "Austin"].set_index("ts_utc")["temp_c"].sort_index()
    return s.reindex(hours).interpolate(limit_direction="both").to_numpy()


def _call_hours(hours: pd.DatetimeIndex) -> np.ndarray:
    """Fraction of each hour covered by a 2025 candidate call (discharge window)."""
    from runway.study import CALLS_PATH
    frac = np.zeros(len(hours))
    if not CALLS_PATH.exists():
        return frac
    calls = pd.read_parquet(CALLS_PATH)
    start_h = hours[0]
    for _, c in calls.iterrows():
        s, e = c["start"], c["start"] + pd.Timedelta(minutes=float(c["duration_min"]))
        i = int((s.floor("h") - start_h) / pd.Timedelta(hours=1))
        while 0 <= i < len(hours) and hours[i] < e:
            lo, hi = max(s, hours[i]), min(e, hours[i] + pd.Timedelta(hours=1))
            frac[i] += max(0.0, (hi - lo) / pd.Timedelta(hours=1))
            i += 1
    return frac


def _registry(rng) -> tuple[pd.DataFrame, pd.DataFrame]:
    n = N_UNITS
    ids = np.arange(n)
    terms = np.where(ids % 2 == 0, 10, 12)
    age = rng.uniform(0.0, 1.0, n) * terms
    install = [SNAPSHOT_UTC - timedelta(days=float(a) * 365.25) for a in age]
    mount = rng.choice(MOUNTS, size=n, p=MOUNT_P)
    shade = np.where(mount == "garage_interior", 0.0, rng.uniform(0.0, 0.8, n))
    sun_true = np.clip(np.array([MOUNT_SUN[m] for m in mount]) * (1.0 - shade) + rng.normal(0, 0.03, n), 0.0, 1.0)
    ua = np.clip(rng.normal(40.0, 5.0, n), 28.0, 55.0)
    solar = np.clip(rng.normal(250.0, 35.0, n), 150.0, 360.0)
    mass = np.clip(rng.normal(3.0e5, 4.0e4, n), 2.0e5, 4.2e5)
    usage = np.exp(rng.normal(0.0, USAGE_SPREAD, n))
    p = load("nominal")
    efc_start = np.maximum(age - 61 / 365.25, 0.0) * p["efc_per_year"] * usage  # counter at the window start
    zips = rng.choice(AUSTIN_ZIPS, size=n)
    reg = pd.DataFrame({
        "unit_id": ids.astype("int32"),
        "install_date": pd.to_datetime(install).tz_convert("UTC"),
        "term_years": terms.astype("int16"),
        "zip": zips,
        "lat": np.round(30.2672 + rng.normal(0, 0.08, n), 4),
        "lon": np.round(-97.7431 + rng.normal(0, 0.09, n), 4),
        "mount": mount,
        "shade_survey": np.round(np.clip(shade + rng.normal(0, 0.1, n), 0, 1), 1),
        "nameplate_kw": np.float32(20.0),
        "capacity_kwh": np.float32(CAPACITY_KWH),
        "firmware": rng.choice(FIRMWARE, size=n, p=(0.2, 0.5, 0.3)),
        "lifetime_kwh_before_window": np.round(efc_start * CAPACITY_KWH, 1).astype("float32"),
        "synthetic": True,
    })
    truth = pd.DataFrame({
        "unit_id": ids.astype("int32"), "age_years_at_snapshot": age, "sun_exposure": sun_true,
        "ua_w_per_k": ua, "solar_gain_w_at_sun1": solar, "thermal_mass_j_per_k": mass,
        "efc_at_window_start": efc_start, "usage_factor": usage,
    })
    return reg, truth


def _mount_shape(mount: np.ndarray, local_hour: np.ndarray) -> np.ndarray:
    """Clear-sky solar weighting by hour for each mount, normalized to a daily mean of 0.4."""
    sun_up = np.clip(np.sin(np.pi * (local_hour - 6.5) / 14.0), 0.0, None)
    peaks = np.array([MOUNT_PEAK_H[m] for m in mount])
    shape = sun_up[None, :] * np.exp(-0.5 * ((local_hour[None, :] - peaks[:, None]) / 3.0) ** 2)
    flat = np.isin(mount, ("garage_interior", "exterior_north"))
    shape[flat] = sun_up[None, :]
    return shape / shape.mean(axis=1, keepdims=True) * 0.4


def generate(seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed + 1000)
    reg, truth = _registry(rng)
    n = N_UNITS
    p = load("nominal")
    hours = _hours()
    local = hours.tz_convert(TZ)
    days = local.normalize()
    day_idx = ((days - days[0]) / pd.Timedelta(days=1)).astype(int).to_numpy()
    n_days = int(day_idx.max()) + 1
    amb = _austin_temps(hours)
    cloud = rng.beta(6.0, 2.0, n_days)
    call_frac = _call_hours(hours)
    mount = reg["mount"].to_numpy()
    shape24 = _mount_shape(mount, np.arange(24) + 0.5)
    garage = mount == "garage_interior"

    ua, solar, mass = truth["ua_w_per_k"].to_numpy(), truth["solar_gain_w_at_sun1"].to_numpy(), truth["thermal_mass_j_per_k"].to_numpy()
    sun = truth["sun_exposure"].to_numpy()
    decay = np.exp(-3600.0 / (mass / ua))

    up_p = rng.beta(40.0, 1.5, n)
    outage = np.zeros((n, n_days), dtype=bool)
    hit = rng.uniform(size=n) < 0.03
    for u in np.where(hit)[0]:
        d0 = int(rng.integers(0, n_days - 2))
        outage[u, d0:d0 + int(rng.integers(2, 11))] = True

    health = np.array([initial_health(max(a - 61 / 365.25, 0.0), t) for a, t in zip(truth["age_years_at_snapshot"], reg["term_years"])])
    health = np.minimum(health, 1.0)
    efc = truth["efc_at_window_start"].to_numpy().copy()
    age_y = np.maximum(truth["age_years_at_snapshot"].to_numpy() - 61 / 365.25, 0.0)
    soh_bias = rng.normal(0.0, SOH_BIAS_SD, n)

    temp = amb[0] + garage * GARAGE_OFFSET_C + solar * sun * 0.3 / ua
    soc = np.ones(n)
    recharge_left = np.zeros(n)
    agg = {k: np.zeros((n, n_days)) for k in ("hb", "t_sum", "t_max", "soc_min", "dis", "chg", "cyc_wear")}
    agg["t_max"][:] = -99.0
    agg["soc_min"][:] = 1.0
    last_seen = np.full((n, n_days), np.nan)
    hours_s = hours.as_unit("s").asi8.astype(float)

    for h in range(len(hours)):
        d = day_idx[h]
        lh = local[h].hour
        dis_kw = EVEN_SHARE_KW * call_frac[h]
        chg_kw = np.where(recharge_left > 0, np.minimum(RECHARGE_KW, recharge_left), 0.0) if dis_kw == 0 else np.zeros(n)
        heat = solar * sun * shape24[:, lh] * cloud[d] + p["joule_w_per_kw2"] * dis_kw ** 2
        t_ss = amb[h] + garage * GARAGE_OFFSET_C + heat / ua
        temp = t_ss + (temp - t_ss) * decay
        e_dis = dis_kw * 1.0
        soc = soc - e_dis / (CAPACITY_KWH * health) + chg_kw / (CAPACITY_KWH * health)
        soc = np.minimum(soc, 1.0)
        recharge_left = recharge_left + e_dis - chg_kw
        if e_dis > 0:
            agg["cyc_wear"][:, d] += age_arrays(p, temp, EVEN_SHARE_KW, 3600.0 * call_frac[h], soc, age_y, efc) - \
                age_arrays(p, temp, 0.0, 3600.0 * call_frac[h], soc, age_y, efc)
            efc = efc + e_dis / (2.0 * CAPACITY_KWH)
        if np.any(chg_kw > 0):
            efc = efc + chg_kw / (2.0 * CAPACITY_KWH)
        agg["dis"][:, d] += e_dis
        agg["chg"][:, d] += chg_kw
        seen = (rng.uniform(size=n) < up_p) & ~outage[:, d]
        reading = np.round(temp + rng.normal(0.0, TEMP_NOISE_C, n), 1)
        agg["hb"][:, d] += seen
        agg["t_sum"][:, d] += np.where(seen, reading, 0.0)
        agg["t_max"][:, d] = np.where(seen, np.maximum(agg["t_max"][:, d], reading), agg["t_max"][:, d])
        agg["soc_min"][:, d] = np.where(seen, np.minimum(agg["soc_min"][:, d], soc), agg["soc_min"][:, d])
        last_seen[:, d] = np.where(seen, hours_s[h], last_seen[:, d])
        if lh == 23:
            day_mean = np.where(agg["hb"][:, d] > 0, agg["t_sum"][:, d] / np.maximum(agg["hb"][:, d], 1), temp)
            health = health - k_cal(p, day_mean + 273.15, 1.0) * power_increment(age_y * 365.25, 1.0, p["pcal"]) - agg["cyc_wear"][:, d]
            age_y = age_y + 1.0 / 365.25
            agg.setdefault("soh", np.zeros((n, n_days)))[:, d] = health

    hb = agg["hb"]
    soh = np.minimum(np.round((agg["soh"] + soh_bias[:, None] + rng.normal(0.0, SOH_NOISE, (n, n_days))) * 1000.0) / 10.0, 100.0)
    for d in range(1, n_days):
        prev = np.where(np.isnan(last_seen[:, d]), last_seen[:, d - 1], last_seen[:, d])
        last_seen[:, d] = prev
    day_dates = (days[0] + pd.to_timedelta(np.arange(n_days), unit="D")).date
    daily = pd.DataFrame({
        "unit_id": np.repeat(np.arange(n, dtype="int32"), n_days),
        "day": np.tile(np.array(day_dates, dtype="datetime64[D]"), n),
        "heartbeats": hb.ravel().astype("int8"),
        "enclosure_mean_c": np.where(hb > 0, agg["t_sum"] / np.maximum(hb, 1), np.nan).ravel().round(2).astype("float32"),
        "enclosure_max_c": np.where(hb > 0, agg["t_max"], np.nan).ravel().astype("float32"),
        "soc_min": np.where(hb > 0, agg["soc_min"], np.nan).ravel().round(3).astype("float32"),
        "discharged_kwh": agg["dis"].ravel().round(2).astype("float32"),
        "charged_kwh": agg["chg"].ravel().round(2).astype("float32"),
        "soh_pct": np.where(hb > 0, soh, np.nan).ravel().astype("float32"),
        "last_seen_utc": pd.to_datetime(last_seen.ravel(), unit="s", utc=True),
    })
    snap_day = (SNAPSHOT_UTC.date() - WINDOW_START.date()).days - 1
    truth["health_at_snapshot"] = agg["soh"][:, snap_day]
    truth["efc_at_snapshot"] = truth["efc_at_window_start"] + (agg["dis"][:, :snap_day + 1].sum(1) + agg["chg"][:, :snap_day + 1].sum(1)) / (2 * CAPACITY_KWH)
    truth["effective_exposure"] = np.clip((solar * sun * 0.3 / ua + garage * GARAGE_OFFSET_C) / (p["solar_gain_w_at_sun1"] * p["solar_daily_mean_fraction"] / p["ua_w_per_k"]), 0.0, 1.0)
    truth["outage_days"] = outage.sum(axis=1)
    return {"registry": reg, "daily": daily, "truth": truth}


def write(seed: int = SEED) -> dict:
    out = generate(seed)
    TELEMETRY.mkdir(parents=True, exist_ok=True)
    for name, path in (("registry", REGISTRY), ("daily", DAILY), ("truth", TRUTH)):
        out[name].to_parquet(path, index=False, compression="zstd")
    manifest = {
        "synthetic": True,
        "note": "Synthetic stand-in for Base fleet telemetry. Not Base data. Drivers: real Austin weather and 2025 call days.",
        "seed": seed,
        "window": [str(WINDOW_START.date()), str((WINDOW_END - pd.Timedelta(days=1)).date())],
        "files": {p.name: {"rows": len(out[k]), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                  for k, p in (("registry", REGISTRY), ("daily", DAILY), ("truth", TRUTH))},
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify() -> list[str]:
    if not MANIFEST.exists():
        return ["data/telemetry/manifest.json missing"]
    m = json.loads(MANIFEST.read_text())
    return [f"telemetry mismatch {name}" for name, info in m["files"].items()
            if not (TELEMETRY / name).exists() or hashlib.sha256((TELEMETRY / name).read_bytes()).hexdigest() != info["sha256"]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate or verify the synthetic Base-like telemetry cache")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args(argv)
    if a.verify:
        problems = verify()
        print("\n".join(problems) or "telemetry ok (synthetic)")
        return 1 if problems else 0
    m = write()
    print(f"synthetic telemetry: {', '.join(f'{k} {v['rows']:,} rows' for k, v in m['files'].items())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
