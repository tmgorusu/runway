"""Austin anomaly response: game-theoretic subgrid targeting with wear leveling.

`python -m runway.austin` writes outputs/austin/events.json and rebuilds the dashboard (web/index.html).

Anomaly rule (fixed before the first run; Austin Energy territory, 2025-04-01 to 2025-09-30,
the window the synthetic telemetry covers). A local day is an anomaly when any of these hold:
  4cp_candidate  the day is a candidate call under CALLING_RULE.md (handoff/calls.parquet)
  price_spike    an hourly LZ_AEN day-ahead price is >= $100/MWh and >= 3x the trailing
                 30-day median of daily maximum LZ_AEN prices
  heat           Austin's daily high exceeds its trailing 30-day mean by more than 2 standard deviations
Requested MW by type (assumption): 4cp_candidate 40, price_spike 30, heat 20; a day with several
types takes the largest. Each response is 90 minutes.

Unit state on the event day comes from the synthetic Base-like telemetry: fitted thermal exposure,
that day's BMS state of health, cycles, and heartbeats (a unit with fewer than 12 heartbeats that day
is treated as stale, so marginal_wear doubles on a high peak score). Ambient is Austin's temperature
at the event hour. SOC is 1.0 at the start of each response (assumption).

Feeder zones are synthetic: 0.06-degree grid cells over the fleet, cells with fewer than 25 units
merged into the nearest larger cell. Each zone's hosting limit is a seeded fraction in [0.35, 0.80]
of its nameplate (assumption). The game itself is documented in runway/austin_game.js; this module
runs the same algorithm (tests check parity with node) and realizes the result at unit level.
"""
from __future__ import annotations

import argparse
import bisect
import dataclasses
import json
import sys
from datetime import timedelta

import numpy as np
import pandas as pd

from runway import telemetry
from runway.allocate import GRID_POINTS, water_fill
from runway.contracts import CAPACITY_KWH, Call, feasible_kw
from runway.fleet import make_fleet
from runway.marginal import call_wear, marginal_grid, unit_arrays, weights
from runway.paths import OUTPUTS, ROOT

EVENTS_PATH = OUTPUTS / "austin" / "events.json"
TZ = "America/Chicago"
WINDOW = (pd.Timestamp("2025-04-01").date(), pd.Timestamp("2025-09-30").date())
MW_BY_TYPE = {"4cp_candidate": 40.0, "price_spike": 30.0, "heat": 20.0}
PRICE_FLOOR = 100.0
PRICE_MULT = 3.0
HEAT_SIGMA = 2.0
TRAIL_DAYS = 30
CELL_DEG = 0.06
MIN_ZONE_UNITS = 25
HOST_RANGE = (0.35, 0.80)
STALE_HEARTBEATS = 12
CURVE_POINTS = 40
ACTIVATION_FRACTION = 0.5     # default activation cost = this fraction of an average zone's unconstrained wear
SHAPLEY_M = 128
SEED = 7
DURATION_MIN = 90.0
LANDMARKS = [("Downtown", 30.2672, -97.7431), ("UT Austin", 30.2849, -97.7341), ("Mueller", 30.2987, -97.7068),
             ("The Domain", 30.4021, -97.7253), ("Austin-Bergstrom Airport", 30.1975, -97.6664)]


# ---- anomalies -------------------------------------------------------------

def _trailing(series: pd.Series, fn) -> pd.Series:
    return getattr(series.shift(1).rolling(TRAIL_DAYS, min_periods=10), fn)()


def detect_anomalies() -> list[dict]:
    from runway import ingest
    from runway.study import CALLS_PATH

    prices = ingest.load("dam_spp")
    aen = prices[prices["location"] == "LZ_AEN"].copy()
    aen["day"] = aen["ts_utc"].dt.tz_convert(TZ).dt.date
    daily_price = aen.groupby("day")["spp_usd_mwh"].max()
    med = _trailing(daily_price, "median")

    w = ingest.load("weather")
    aus = w[w["city"] == "Austin"].copy()
    aus["day"] = aus["ts_utc"].dt.tz_convert(TZ).dt.date
    daily_temp = aus.groupby("day")["temp_c"].max()
    t_mean, t_std = _trailing(daily_temp, "mean"), _trailing(daily_temp, "std")

    load = ingest.load("ercot_load")
    load["day"] = (load["ts_utc"].dt.tz_convert(TZ) - pd.Timedelta(minutes=30)).dt.date
    peak_ts = load.sort_values(["day", "forecast_mw", "ts_utc"], ascending=[True, False, True]).groupby("day").head(1)
    peak_ts = dict(zip(peak_ts["day"], peak_ts["ts_utc"] - pd.Timedelta(minutes=30)))

    calls = pd.read_parquet(CALLS_PATH) if CALLS_PATH.exists() else pd.DataFrame(columns=["start", "hit", "peak_odds"])
    calls["day"] = calls["start"].dt.tz_convert(TZ).dt.date
    call_by_day = {r.day: r for r in calls.itertuples()}

    events = []
    for day in sorted(set(daily_price.index) | set(daily_temp.index)):
        if not (WINDOW[0] <= day <= WINDOW[1]):
            continue
        types, severity = [], 0.0
        price, base = daily_price.get(day, np.nan), med.get(day, np.nan)
        if price >= PRICE_FLOOR and base > 0 and price >= PRICE_MULT * base:
            types.append("price_spike")
            severity = max(severity, float(np.log2(price / base) / 3.0))
        temp, mu, sd = daily_temp.get(day, np.nan), t_mean.get(day, np.nan), t_std.get(day, np.nan)
        if sd > 0 and temp > mu + HEAT_SIGMA * sd:
            types.append("heat")
            severity = max(severity, float((temp - mu) / sd / 4.0))
        call = call_by_day.get(day)
        if call is not None:
            types.append("4cp_candidate")
            severity = max(severity, float(call.peak_odds))
        if not types:
            continue
        if call is not None:
            start = call.start
        elif "price_spike" in types:
            day_rows = aen[aen["day"] == day]
            start = day_rows.loc[day_rows["spp_usd_mwh"].idxmax(), "ts_utc"]
        else:
            start = peak_ts[day] - pd.Timedelta(minutes=45)
        local_day = aus[aus["day"] == day]
        amb_row = aus.iloc[(aus["ts_utc"] - start.floor("h")).abs().argmin()]
        events.append({
            "day": day.isoformat(),
            "start_utc": pd.Timestamp(start).isoformat(),
            "types": types,
            "severity": round(min(1.0, severity), 4),
            "mw": max(MW_BY_TYPE[t] for t in types),
            "hit_4cp": bool(call.hit) if call is not None else False,
            "ambient_c": round(float(amb_row["temp_c"]), 2),
            "price_max": round(float(price), 2),
            "price_trailing_median": round(float(base), 2) if base == base else None,
            "temp_max": round(float(temp), 2),
            "price_24h": [round(float(v), 2) for v in aen[aen["day"] == day].sort_values("ts_utc")["spp_usd_mwh"]][:24],
            "temp_24h": [round(float(v), 1) for v in local_day.sort_values("ts_utc")["temp_c"]][:24],
        })
    return events


# ---- zones -----------------------------------------------------------------

def build_zones(reg: pd.DataFrame) -> tuple[np.ndarray, list[dict]]:
    lat, lon = reg["lat"].to_numpy(), reg["lon"].to_numpy()
    lat0, lon0 = np.floor(lat.min() / CELL_DEG) * CELL_DEG, np.floor(lon.min() / CELL_DEG) * CELL_DEG
    ci = np.floor((lat - lat0) / CELL_DEG).astype(int)
    cj = np.floor((lon - lon0) / CELL_DEG).astype(int)
    cells = {}
    for u, key in enumerate(zip(ci, cj)):
        cells.setdefault(key, []).append(u)
    big = {k: v for k, v in cells.items() if len(v) >= MIN_ZONE_UNITS}
    keys = sorted(big)
    centers = {k: (lat0 + (k[0] + 0.5) * CELL_DEG, lon0 + (k[1] + 0.5) * CELL_DEG) for k in cells}
    members = {k: list(big[k]) for k in keys}
    for k, v in cells.items():
        if k in big:
            continue
        cl = centers[k]
        near = min(keys, key=lambda b: ((centers[b][0] - cl[0]) ** 2 + ((centers[b][1] - cl[1]) * np.cos(np.radians(30.27))) ** 2, b))
        members[near].extend(v)
    rng = np.random.default_rng(SEED + 17)
    zone_of = np.zeros(len(reg), dtype=int)
    zones = []
    for z, k in enumerate(keys):
        ids = sorted(members[k])
        zone_of[ids] = z
        zones.append({
            "id": f"F{k[0]:02d}{k[1]:02d}",
            "cell": [round(lat0 + k[0] * CELL_DEG, 4), round(lat0 + (k[0] + 1) * CELL_DEG, 4),
                     round(lon0 + k[1] * CELL_DEG, 4), round(lon0 + (k[1] + 1) * CELL_DEG, 4)],
            "n_units": len(ids),
            "host_frac": round(float(rng.uniform(*HOST_RANGE)), 3),
        })
    return zone_of, zones


# ---- per-event unit state and zone supply curves ---------------------------

def event_units(fleet, daily: pd.DataFrame, event: dict):
    """Fleet copies as of the event: age, telemetry health and heartbeats, SOC 1.0."""
    start = pd.Timestamp(event["start_utc"])
    day = pd.Timestamp(event["day"])
    rows = daily[daily["day"] == day].set_index("unit_id")
    prev = daily[daily["day"] == day - pd.Timedelta(days=1)].set_index("unit_id")
    dy = (start - pd.Timestamp(telemetry.SNAPSHOT_UTC)).total_seconds() / (365.25 * 86400.0)
    out, stale = [], []
    for u in fleet:
        r = rows.loc[u.unit_id] if u.unit_id in rows.index else None
        health = float(r["soh_pct"]) / 100.0 if r is not None and r["soh_pct"] == r["soh_pct"] else u.health
        fresh = r is not None and r["heartbeats"] >= STALE_HEARTBEATS
        if fresh:
            last = (start - pd.Timedelta(minutes=5)).to_pydatetime()
        else:
            p = prev.loc[u.unit_id]["last_seen_utc"] if u.unit_id in prev.index else pd.NaT
            last = p.to_pydatetime() if p == p else (start - pd.Timedelta(days=2)).to_pydatetime()
            stale.append(u.unit_id)
        out.append(dataclasses.replace(u, soc=1.0, health=min(health, 1.0), age_years=max(u.age_years + dy, 0.0),
                                       last_seen=last, metadata=dict(u.metadata)))
    return out, stale


def _round_sig(x, sig=6):
    return float(f"{x:.{sig}g}")


def zone_curves(units, zone_of, zones, call: Call):
    arr = unit_arrays(units)
    caps = np.array([feasible_kw(u, call.hours) for u in units])
    grid = caps[:, None] * np.linspace(0.0, 1.0, GRID_POINTS)[None, :]
    costs = np.maximum.accumulate(marginal_grid(arr, call, grid, call.duration_min * 60.0), axis=1)
    w = weights(arr, call)
    p_even = np.arange(0, 21, dtype=float)
    even_power = np.minimum(p_even[None, :], caps[:, None])
    even_wear = call_wear(arr, call.ambient_c, even_power, call.duration_min * 60.0) * w[:, None]
    out = []
    for z, meta in enumerate(zones):
        idx = np.where(zone_of == z)[0]
        step = caps[idx] / (GRID_POINTS - 1)
        inc_cost = costs[idx, 1:].ravel()
        inc_size = np.repeat(step, GRID_POINTS - 1)
        inc_unit = np.repeat(idx, GRID_POINTS - 1)
        inc_j = np.tile(np.arange(GRID_POINTS - 1), len(idx))
        order = np.lexsort((inc_j, inc_unit, inc_cost))
        c, s = inc_cost[order], inc_size[order]
        cum_kw, cum_cost = np.cumsum(s), np.cumsum(c * s)
        targets = np.linspace(0.0, cum_kw[-1], CURVE_POINTS)[1:]
        pick = np.unique(np.minimum(np.searchsorted(cum_kw, targets - 1e-9), len(cum_kw) - 1))
        kw = [0.0] + [round(float(cum_kw[i]), 3) for i in pick]
        lam = [_round_sig(float(c[0]))] + [_round_sig(float(c[i])) for i in pick]
        cost = [0.0] + [_round_sig(float(cum_cost[i])) for i in pick]
        out.append({"id": meta["id"], "kw": kw, "lam": lam, "cost": cost,
                    "host_kw": round(meta["host_frac"] * 20.0 * len(idx), 3),
                    "even": [_round_sig(float(v)) for v in even_wear[idx].sum(axis=0)]})
    return out, dict(arr=arr, caps=caps, grid=grid, costs=costs, w=w)


# ---- the game (same algorithm as runway/austin_game.js) ---------------------

def _last_le(arr, x):
    return bisect.bisect_right(arr, x) - 1


def _supply(g, lam, cap):
    k = _last_le(g["lam"], lam)
    return 0.0 if k < 0 else min(g["kw"][k], cap)


def _cost_at(g, kw):
    k = _last_le(g["kw"], kw)
    if k < 0:
        return 0.0
    if k >= len(g["kw"]) - 1:
        return g["cost"][-1]
    span = g["kw"][k + 1] - g["kw"][k]
    return g["cost"][k] + ((kw - g["kw"][k]) / span * (g["cost"][k + 1] - g["cost"][k]) if span > 0 else 0.0)


def _sum(xs):
    total = 0.0
    for x in xs:
        total += x
    return total


def equilibrium(zones, ids, R, host_mul):
    caps = [min(zones[i]["host_kw"] * host_mul, zones[i]["kw"][-1]) for i in ids]
    total_cap = _sum(caps)
    if not ids:
        return {"lam": None, "dispatch": [], "shortfall": R, "wear": 0.0}
    if total_cap <= R:
        wear = _sum(_cost_at(zones[i], caps[j]) for j, i in enumerate(ids))
        return {"lam": max(zones[i]["lam"][-1] for i in ids), "dispatch": caps, "shortfall": R - total_cap, "wear": wear}
    levels = sorted({v for i in ids for v in zones[i]["lam"]})

    def total(lam):
        return _sum(_supply(zones[i], lam, caps[j]) for j, i in enumerate(ids))

    lo, hi = 0, len(levels) - 1
    while lo < hi:
        mid = (lo + hi) >> 1
        if total(levels[mid]) >= R:
            hi = mid
        else:
            lo = mid + 1
    top = [_supply(zones[i], levels[lo], caps[j]) for j, i in enumerate(ids)]
    base = [min(_supply(zones[i], levels[lo - 1], caps[j]), top[j]) if lo > 0 else 0.0 for j, i in enumerate(ids)]
    room = [t - b for t, b in zip(top, base)]
    room_sum = _sum(room)
    gap = R - _sum(base)
    alpha = gap / room_sum if room_sum > 0 else 0.0
    disp = [b + alpha * r for b, r in zip(base, room)]
    wear = _sum(_cost_at(zones[i], disp[j]) for j, i in enumerate(ids))
    return {"lam": levels[lo], "dispatch": disp, "shortfall": 0.0, "wear": wear}


def _penalty(zones):
    return 10 * max(z["lam"][-1] for z in zones)


def coalition_cost(zones, ids, R, F, host_mul, pen):
    eq = equilibrium(zones, ids, R, host_mul)
    active = sum(1 for d in eq["dispatch"] if d > 1e-9)
    return eq["wear"] + F * active + pen * eq["shortfall"], eq


def select(zones, R, F, host_mul, out=()):
    pen = _penalty(zones)
    allowed = [i for i, z in enumerate(zones) if z["id"] not in set(out)]
    eq0 = equilibrium(zones, allowed, R, host_mul)
    S = [i for j, i in enumerate(allowed) if eq0["dispatch"][j] > 1e-9]
    best = coalition_cost(zones, S, R, F, host_mul, pen)[0]
    for _ in range(2):
        while True:
            pick, pick_cost = -1, best
            for g in S:
                c = coalition_cost(zones, [x for x in S if x != g], R, F, host_mul, pen)[0]
                if c < pick_cost - 1e-15:
                    pick, pick_cost = g, c
            if pick < 0:
                break
            S = [x for x in S if x != pick]
            best = pick_cost
        added = False
        while True:
            pick, pick_cost = -1, best
            for g in allowed:
                if g in S:
                    continue
                c = coalition_cost(zones, sorted(S + [g]), R, F, host_mul, pen)[0]
                if c < pick_cost - 1e-15:
                    pick, pick_cost = g, c
            if pick < 0:
                break
            S = sorted(S + [pick])
            best = pick_cost
            added = True
        if not added:
            break
    total, eq = coalition_cost(zones, S, R, F, host_mul, pen)
    return S, total, eq, pen


class _Mulberry32:
    def __init__(self, seed):
        self.a = seed & 0xFFFFFFFF

    def __call__(self):
        self.a = (self.a + 0x6D2B79F5) & 0xFFFFFFFF
        t = self.a
        t = _imul(t ^ (t >> 15), t | 1)
        t ^= (t + _imul(t ^ (t >> 7), t | 61)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296


def _imul(a, b):
    return (a * b) & 0xFFFFFFFF


def shapley(zones, members, R, F, host_mul, pen, M=SHAPLEY_M, seed=SEED):
    rand = _Mulberry32(seed)
    n = len(members)
    phi = [0.0] * n
    empty = coalition_cost(zones, [], R, F, host_mul, pen)[0]
    for _ in range(M):
        order = list(range(n))
        for j in range(n - 1, 0, -1):
            r = int(rand() * (j + 1))
            order[j], order[r] = order[r], order[j]
        prefix, prev = [], empty
        for j in order:
            prefix = sorted(prefix + [members[j]])
            c = coalition_cost(zones, prefix, R, F, host_mul, pen)[0]
            phi[j] += (prev - c) / M
            prev = c
    return phi, empty - coalition_cost(zones, members, R, F, host_mul, pen)[0]


def solve(zones, R, F, host_mul=1.0, out=(), M=SHAPLEY_M, seed=SEED) -> dict:
    S, total, eq, pen = select(zones, R, F, host_mul, out)
    phi, grand = shapley(zones, S, R, F, host_mul, pen, M, seed)
    return {"lam": eq["lam"], "members": [zones[i]["id"] for i in S], "dispatch_kw": eq["dispatch"], "wear": eq["wear"],
            "shortfall_kw": eq["shortfall"], "total_cost": total, "shapley": phi, "coalition_value": grand}


def default_activation_cost(zones, R) -> float:
    eq = equilibrium(zones, list(range(len(zones))), R, 1e9)
    active = max(1, sum(1 for d in eq["dispatch"] if d > 1e-9))
    return ACTIVATION_FRACTION * eq["wear"] / active


# ---- unit-level realization -------------------------------------------------

def unit_dispatch(zone_of, zones, ctx, result: dict) -> np.ndarray:
    """Each targeted zone water-fills its own units to the zone's equilibrium dispatch."""
    power = np.zeros(len(zone_of))
    zid = {z["id"]: k for k, z in enumerate(zones)}
    for zone_id, kw in zip(result["members"], result["dispatch_kw"]):
        idx = np.where(zone_of == zid[zone_id])[0]
        p, _ = water_fill(kw, ctx["caps"][idx], ctx["grid"][idx], ctx["costs"][idx])
        power[idx] = p
    return power


def realize(units, zone_of, zones, curves, ctx, call: Call, result: dict) -> dict:
    """Dispatch units inside each targeted zone by water-fill; compare with even spread and the unconstrained water-fill."""
    arr, caps, grid, costs, w = ctx["arr"], ctx["caps"], ctx["grid"], ctx["costs"], ctx["w"]
    dt = call.duration_min * 60.0
    R = call.kw_requested
    power = unit_dispatch(zone_of, zones, ctx, result)
    even_p = np.minimum(caps, R / len(units))
    free_p, free_short = water_fill(R, caps, grid, costs)
    host = np.array([c["host_kw"] for c in curves])

    def feeder_violations(p):
        per_zone = np.bincount(zone_of, weights=p, minlength=len(zones))
        return int(np.sum(per_zone > host + 1e-6))

    def true_wear(p):
        return float(np.sum(call_wear(arr, call.ambient_c, p, dt)))

    energy = np.array([max(0.0, u.capacity_kwh * u.health * (u.soc - u.reserve_frac)) for u in units])
    return {
        "game": {"true_wear": true_wear(power), "delivered_kw": float(power.sum()), "feeder_violations": feeder_violations(power),
                 "reserve_violations": int(np.sum(power * call.hours > energy + 1e-9)), "units_dispatched": int(np.sum(power > 1e-9))},
        "even": {"true_wear": true_wear(even_p), "delivered_kw": float(even_p.sum()), "feeder_violations": feeder_violations(even_p),
                 "reserve_violations": int(np.sum(even_p * call.hours > energy + 1e-9)), "units_dispatched": int(np.sum(even_p > 1e-9))},
        "unconstrained": {"true_wear": true_wear(free_p), "delivered_kw": float(free_p.sum()), "feeder_violations": feeder_violations(free_p),
                          "reserve_violations": int(np.sum(free_p * call.hours > energy + 1e-9)), "units_dispatched": int(np.sum(free_p > 1e-9))},
        "power_by_zone_kw": [round(float(v), 3) for v in np.bincount(zone_of, weights=power, minlength=len(zones))],
    }


# ---- build ------------------------------------------------------------------

def build() -> dict:
    if telemetry.verify():
        telemetry.write()
    reg = pd.read_parquet(telemetry.REGISTRY)
    daily = pd.read_parquet(telemetry.DAILY)
    daily["day"] = pd.to_datetime(daily["day"])
    fleet = make_fleet(len(reg))
    zone_of, zones = build_zones(reg)
    exposure = np.array([u.sun_exposure for u in fleet])
    events = detect_anomalies()
    for ev in events:
        start = pd.Timestamp(ev["start_utc"]).to_pydatetime()
        call = Call(f"aen-{ev['day']}", start, DURATION_MIN, ev["mw"], "Austin Energy", max(ev["severity"], 0.9 if "4cp_candidate" in ev["types"] else ev["severity"]), ev["ambient_c"])
        units, stale = event_units(fleet, daily, ev)
        curves, ctx = zone_curves(units, zone_of, zones, call)
        F = default_activation_cost(curves, call.kw_requested)
        res = solve(curves, call.kw_requested, F)
        ev.update({"zones": curves, "stale_units": stale, "activation_cost": F, "peak_score": call.peak_odds,
                   "result": res, "units": realize(units, zone_of, zones, curves, ctx, call, res)})
    out = {
        "synthetic_fleet": True,
        "note": ("Synthetic fleet and synthetic feeder zones. LZ_AEN day-ahead prices, ERCOT load, the 4CP intervals, and Austin weather "
                 "are real 2025 data. Nothing here is Austin Energy's feeder map, Base Power telemetry, or a claim about how either operates."),
        "rule": {"price_floor_usd_mwh": PRICE_FLOOR, "price_multiple": PRICE_MULT, "heat_sigma": HEAT_SIGMA, "trailing_days": TRAIL_DAYS,
                 "mw_by_type": MW_BY_TYPE, "window": [WINDOW[0].isoformat(), WINDOW[1].isoformat()], "duration_min": DURATION_MIN},
        "game": {"activation_fraction": ACTIVATION_FRACTION, "shapley_permutations": SHAPLEY_M, "seed": SEED,
                 "shortfall_penalty": "10x the highest marginal wear in the zone curves, per kW"},
        "zones": zones,
        "units": [[round(float(a), 4), round(float(b), 4), round(float(e), 3), int(z)]
                  for a, b, e, z in zip(reg["lat"], reg["lon"], exposure, zone_of)],
        "landmarks": [{"name": n, "lat": la, "lon": lo} for n, la, lo in LANDMARKS],
        "events": events,
    }
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVENTS_PATH.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    from runway import web
    web.build()
    return out


# ---- dispatch plan export (usable with a real fleet that follows the telemetry schema) ----

def _event_context(day: str, mw: float | None = None):
    reg = pd.read_parquet(telemetry.REGISTRY)
    daily = pd.read_parquet(telemetry.DAILY)
    daily["day"] = pd.to_datetime(daily["day"])
    zone_of, zones = build_zones(reg)
    ev = next((e for e in detect_anomalies() if e["day"] == day), None)
    if ev is None:
        raise SystemExit(f"{day} is not an anomaly day in the April–September 2025 window")
    mw = ev["mw"] if mw is None else mw
    call = Call(f"aen-{day}", pd.Timestamp(ev["start_utc"]).to_pydatetime(), DURATION_MIN, mw, "Austin Energy",
                max(ev["severity"], 0.9 if "4cp_candidate" in ev["types"] else ev["severity"]), ev["ambient_c"])
    units, stale = event_units(make_fleet(len(reg)), daily, ev)
    curves, ctx = zone_curves(units, zone_of, zones, call)
    return reg, zone_of, zones, units, stale, curves, ctx, call


def plan(day: str, mw: float | None = None, activation: float = 1.0, hosting: float = 1.0, out_zones=(), path=None) -> dict:
    """Solve one anomaly day and write per-unit setpoints to CSV."""
    reg, zone_of, zones, units, stale, curves, ctx, call = _event_context(day, mw)
    F = default_activation_cost(curves, call.kw_requested) * activation
    res = solve(curves, call.kw_requested, F, hosting, out_zones)
    power = unit_dispatch(zone_of, zones, ctx, res)
    mw_cost = marginal_grid(ctx["arr"], call, power[:, None], call.duration_min * 60.0)[:, 0]
    stale_set = set(stale)
    rows = pd.DataFrame({
        "unit_id": reg["unit_id"], "zone": [zones[z]["id"] for z in zone_of], "lat": reg["lat"], "lon": reg["lon"],
        "power_kw": np.round(power, 4), "feasible_kw": np.round(ctx["caps"], 4),
        "marginal_wear_at_setpoint": np.where(power > 0, mw_cost, np.nan), "stale_telemetry": [u in stale_set for u in reg["unit_id"]],
    })
    path = path or (OUTPUTS / "austin" / f"plan_{day}.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(path, index=False)
    return {"path": path, "result": res, "delivered_kw": float(power.sum()), "units_dispatched": int((power > 0).sum()),
            "requested_kw": call.kw_requested}


# ---- scale benchmark ----------------------------------------------------------

def bench_game(sizes=(4000, 10000, 100000), day: str = "2025-07-30", path=None) -> dict:
    """Time the zone supply curves, the game (with Shapley), and the unit-level dispatch at several fleet sizes."""
    import platform
    import time
    reg = pd.read_parquet(telemetry.REGISTRY)
    _, zones = build_zones(reg)
    ev = next(e for e in detect_anomalies() if e["day"] == day)
    rows = []
    for n in sizes:
        fleet = make_fleet(n)
        units = [dataclasses.replace(u, soc=1.0, last_seen=None, metadata=dict(u.metadata)) for u in fleet]
        zone_of = np.arange(n) % len(zones)
        call = Call("bench", pd.Timestamp(ev["start_utc"]).to_pydatetime(), DURATION_MIN, ev["mw"] * n / 4000.0,
                    "Austin Energy", 0.98, ev["ambient_c"])
        t0 = time.perf_counter()
        curves, ctx = zone_curves(units, zone_of, zones, call)
        t1 = time.perf_counter()
        res = solve(curves, call.kw_requested, default_activation_cost(curves, call.kw_requested))
        t2 = time.perf_counter()
        power = unit_dispatch(zone_of, zones, ctx, res)
        t3 = time.perf_counter()
        rows.append({"n_units": n, "mw_requested": call.mw_requested, "curves_ms": 1000 * (t1 - t0), "game_ms": 1000 * (t2 - t1),
                     "unit_dispatch_ms": 1000 * (t3 - t2), "total_ms": 1000 * (t3 - t0), "zones_targeted": len(res["members"]),
                     "delivered_kw": float(power.sum())})
    out = {"synthetic_fleet": True, "event_day": day, "zones": len(zones), "shapley_permutations": SHAPLEY_M,
           "cpu": platform.processor() or platform.machine(), "python": platform.python_version(),
           "note": "Fleets above 4,000 use the assumed-distribution fallback and round-robin zone assignment; timing only.",
           "results": rows}
    try:
        from runway.bench import cpu_model
        out["cpu"] = cpu_model()
    except Exception:
        pass
    path = path or (OUTPUTS / "bench" / "game.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan", metavar="DAY", help="write per-unit setpoints for one anomaly day (YYYY-MM-DD) and exit")
    ap.add_argument("--mw", type=float, help="requested MW (default: the anomaly type's default)")
    ap.add_argument("--activation", type=float, default=1.0, help="zone activation cost, multiple of the default")
    ap.add_argument("--hosting", type=float, default=1.0, help="feeder hosting limits, multiple of the synthetic limits")
    ap.add_argument("--out-zone", action="append", default=[], help="zone id to treat as out (repeatable)")
    ap.add_argument("--bench", action="store_true", help="time the game at 4,000 / 10,000 / 100,000 units and exit")
    a = ap.parse_args(argv)
    if a.plan:
        r = plan(a.plan, a.mw, a.activation, a.hosting, a.out_zone)
        print(f"{a.plan}: {len(r['result']['members'])} zones, {r['units_dispatched']:,} batteries, "
              f"{r['delivered_kw'] / 1000:.3f} of {r['requested_kw'] / 1000:.3f} MW -> {r['path'].relative_to(ROOT)} (synthetic fleet)")
        return 0
    if a.bench:
        for row in bench_game()["results"]:
            print(f"{row['n_units']:>7,} units  curves {row['curves_ms']:7.0f} ms  game {row['game_ms']:6.0f} ms  "
                  f"unit dispatch {row['unit_dispatch_ms']:6.0f} ms  total {row['total_ms']:7.0f} ms")
        return 0
    out = build()
    ev = out["events"]
    print(f"{len(ev)} anomaly days (synthetic fleet, real Austin Energy prices and weather); {len(out['zones'])} synthetic feeder zones")
    for e in ev[:5]:
        r = e["result"]
        print(f"  {e['day']} {'+'.join(e['types']):28s} {e['mw']:.0f} MW -> {len(r['members'])} zones, λ*={r['lam']:.3g}, "
              f"wear game {e['units']['game']['true_wear']:.4f} vs even {e['units']['even']['true_wear']:.4f}")
    print(f"wrote {EVENTS_PATH.relative_to(ROOT)} and web/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
