"""Execute CALLING_RULE.md on the 2025 cache and write the candidate calls for Wear.

Rule (VISION section 4, text owned by Wear in CALLING_RULE.md): a day in
America/Chicago, June through September 2025, is a candidate when its maximum
day-ahead ERCOT system forecast is at least THRESHOLD times the maximum daily
day-ahead peak from the first of that month through that day, inclusive. One
call per candidate day, starting 45 minutes before the forecast peak and lasting
90 minutes. peak_odds is that ratio clipped to [0, 1]; a score, not a probability.
A call hits when a published 4CP interval falls inside the window.

Conventions this module fixes (hourly data): the forecast is hour-ending, so the
forecast peak instant is the middle of the peak hour (hour-ending minus 30 min).
A 4CP interval (15 minutes, interval-ending) falls inside the window when it
overlaps it. ERCOT system load is a proxy for the Austin Energy, GVEC, and
CoServ hunts.

The threshold is parsed from CALLING_RULE.md, never hard-coded, and the file's
sha256 is written into every output as rule_hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd

from runway import ingest

ROOT = ingest.ROOT
RULE = ROOT / "CALLING_RULE.md"
HANDOFF = ROOT / "handoff"
CALLS = HANDOFF / "calls.parquet"
HIT_FLAGS = HANDOFF / "hit_flags.json"
SENSITIVITY = HANDOFF / "sensitivity.json"
TZ = "America/Chicago"
MONTHS = (6, 7, 8, 9)
LEAD_MIN = 45
DURATION_MIN = 90
MW_REQUESTED = 40.0
UTILITY = "Austin Energy"
SOURCE = "4cp_candidate"
COLUMNS = [
    "call_id", "utility", "start", "duration_min", "mw_requested", "peak_odds", "ambient_c",
    "source", "hit", "miss_reason", "rule_hash", "forecast_peak_mw",
]


def rule_hash(path: Path = RULE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rule_threshold(path: Path = RULE) -> float:
    """Read the threshold from the rule text: `threshold: X` or `at least X times`."""
    text = path.read_text()
    for pattern in (r"threshold\W{0,6}([01]\.\d+)", r"at least\s+\**([01]\.\d+)\**\s+times"):
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return float(m.group(1))
    raise ValueError(f"no threshold found in {path}")


def daily_peaks(load: pd.DataFrame) -> pd.DataFrame:
    """One row per local day: forecast peak MW and the peak instant (UTC)."""
    df = load.dropna(subset=["forecast_mw"]).copy()
    df["local"] = df["ts_utc"].dt.tz_convert(TZ)
    df["day"] = (df["local"] - pd.Timedelta(minutes=30)).dt.date  # hour-ending -> hour it covers
    df = df[pd.to_datetime(df["day"]).dt.month.isin(MONTHS)]
    idx = df.sort_values(["day", "forecast_mw", "ts_utc"], ascending=[True, False, True]).groupby("day").head(1)
    out = idx[["day", "forecast_mw", "ts_utc"]].rename(columns={"forecast_mw": "forecast_peak_mw"})
    out["peak_utc"] = out.pop("ts_utc") - pd.Timedelta(minutes=30)
    out = out.sort_values("day").reset_index(drop=True)
    month = pd.to_datetime(out["day"]).dt.month
    out["running_max_mw"] = out.groupby(month)["forecast_peak_mw"].cummax()
    out["ratio"] = out["forecast_peak_mw"] / out["running_max_mw"]
    return out


def ambient_at(weather: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, city: str = "Austin") -> float:
    w = weather[weather["city"] == city]
    inside = w[(w["ts_utc"] >= start.floor("h")) & (w["ts_utc"] <= end.ceil("h"))]
    if len(inside):
        return float(inside["temp_c"].mean())
    return float(w.iloc[(w["ts_utc"] - start).abs().argmin()]["temp_c"])


def build_calls(load, cp, weather, threshold: float, rhash: str) -> tuple[pd.DataFrame, list[dict]]:
    peaks = daily_peaks(load)
    cand = peaks[peaks["ratio"] >= threshold].copy()
    rows = []
    for _, d in cand.iterrows():
        start = d["peak_utc"] - pd.Timedelta(minutes=LEAD_MIN)
        end = start + pd.Timedelta(minutes=DURATION_MIN)
        inside = cp[(cp["interval_start_utc"] < end) & (cp["interval_end_utc"] > start)]
        same_day = cp[cp["interval_end_utc"].dt.tz_convert(TZ).dt.date == d["day"]]
        if len(inside):
            miss_reason = ""
        elif len(same_day):
            miss_reason = "4cp_same_day_outside_window"
        else:
            miss_reason = "no_4cp_on_day"
        rows.append(
            {
                "call_id": f"4cp-{d['day'].isoformat()}",
                "utility": UTILITY,
                "start": start,
                "duration_min": DURATION_MIN,
                "mw_requested": MW_REQUESTED,
                "peak_odds": float(min(1.0, max(0.0, d["ratio"]))),
                "ambient_c": ambient_at(weather, start, end),
                "source": SOURCE,
                "hit": bool(len(inside)),
                "miss_reason": miss_reason,
                "rule_hash": rhash,
                "forecast_peak_mw": float(d["forecast_peak_mw"]),
            }
        )
    calls = pd.DataFrame(rows, columns=COLUMNS)
    flags = []
    for _, c in cp.iterrows():
        s, e = c["interval_start_utc"], c["interval_end_utc"]
        match = calls[(calls["start"] < e) & (calls["start"] + pd.Timedelta(minutes=DURATION_MIN) > s)]
        day = c["interval_end_utc"].tz_convert(TZ).date()
        if len(match):
            reason = ""
        elif (calls["call_id"] == f"4cp-{day.isoformat()}").any():
            reason = "call_window_missed_interval"
        else:
            reason = "day_not_candidate"
        flags.append(
            {
                "month": c["month"],
                "interval_end_local": c["interval_end_local"],
                "hit": bool(len(match)),
                "call_id": match["call_id"].iloc[0] if len(match) else None,
                "miss_reason": reason,
            }
        )
    return calls, flags


def run(threshold: float | None = None, rule_path: Path = RULE, fixture: bool = False, write: bool = True):
    rhash = rule_hash(rule_path)
    file_threshold = rule_threshold(rule_path)
    threshold = file_threshold if threshold is None else threshold
    load, cp, weather = (ingest.load(n, fixture=fixture) for n in ("ercot_load", "published_4cp", "weather"))
    calls, flags = build_calls(load, cp, weather, threshold, rhash)
    if write:
        HANDOFF.mkdir(exist_ok=True)
        calls.to_parquet(CALLS, index=False)
        HIT_FLAGS.write_text(
            json.dumps(
                {
                    "rule_hash": rhash,
                    "threshold": threshold,
                    "system_load_proxy": True,
                    "n_calls": int(len(calls)),
                    "n_hits": int(calls["hit"].sum()),
                    "published_4cp": flags,
                },
                indent=2,
            )
            + "\n"
        )
    return calls, flags


def sensitivity(rule_path: Path = RULE) -> dict:
    """0.96 and 0.98 are only ever written as a pair, in a side file."""
    out = {"rule_hash": rule_hash(rule_path), "note": "sensitivity only; the rule stays at its committed threshold"}
    for t in (0.96, 0.98):
        calls, flags = run(threshold=t, rule_path=rule_path, write=False)
        out[f"{t:.2f}"] = {"n_calls": int(len(calls)), "n_hits": int(calls["hit"].sum()),
                           "4cp_caught": sum(f["hit"] for f in flags)}
    SENSITIVITY.write_text(json.dumps(out, indent=2) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Execute CALLING_RULE.md and write handoff/calls.parquet")
    ap.add_argument("--sensitivity", action="store_true", help="also write the 0.96/0.98 pair side file")
    args = ap.parse_args(argv)
    if not RULE.exists():
        print("CALLING_RULE.md is not committed yet; Wear owns it.", file=sys.stderr)
        return 2
    calls, flags = run()
    print(f"rule_hash={rule_hash()[:12]} threshold={rule_threshold()} calls={len(calls)} hits={int(calls['hit'].sum())}")
    for f in flags:
        print(f"  4CP {f['interval_end_local']}: {'hit ' + f['call_id'] if f['hit'] else 'miss (' + f['miss_reason'] + ')'}")
    print("ERCOT system load is a proxy for Austin Energy / GVEC / CoServ. peak_odds is a score, not a probability.")
    if args.sensitivity:
        print(json.dumps(sensitivity(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
