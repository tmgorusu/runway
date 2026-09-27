"""Cache 2025 ERCOT and weather data as Parquet so every later step runs offline.

Sources (all public, no keys):
- ERCOT system demand and the day-ahead system demand forecast, hourly: EIA-930
  balancing-authority files for ERCO (https://www.eia.gov/electricity/gridmonitor/).
- Published 2025 4CP intervals: ERCOT report NP9-83-M "Four Coincident Peak
  Calculations" (MIS reportTypeId 13037). Times are the 15-minute interval
  ending, Central Prevailing Time.
- Load-zone day-ahead settlement point prices: ERCOT NP4-180-ER via gridstatus.
  Cached only; allocate.py never imports prices.
- Hourly 2 m temperature for Austin, Seguin, Denton: Open-Meteo archive (ERA5).

`python -m runway.ingest` refreshes the cache (network). `--offline` verifies the
cache against data/manifest.json and never touches the network.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PARQUET = DATA / "parquet"
FIXTURE = DATA / "fixture"
MANIFEST = DATA / "manifest.json"
TZ = "America/Chicago"
YEAR = 2025

EIA_URLS = [
    "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_BALANCE_2025_Jan_Jun.csv",
    "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_BALANCE_2025_Jul_Dec.csv",
]
ERCOT_4CP_LIST = "https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId=13037"
ERCOT_DOWNLOAD = "https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={}"
OPEN_METEO = (
    "https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
    "&start_date=2025-01-01&end_date=2025-12-31&hourly=temperature_2m&timezone=UTC"
)
CITIES = {"Austin": (30.2672, -97.7431), "Seguin": (29.5688, -97.9647), "Denton": (33.2148, -97.1331)}
LOAD_ZONES = ["LZ_AEN", "LZ_CPS", "LZ_HOUSTON", "LZ_LCRA", "LZ_NORTH", "LZ_RAYBN", "LZ_SOUTH", "LZ_WEST"]

FILES = ("ercot_load", "published_4cp", "weather", "dam_spp")
REQUIRED = ("ercot_load", "published_4cp", "weather")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "runway-hackathon/0.1"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def fetch_ercot_load() -> pd.DataFrame:
    frames = []
    for url in EIA_URLS:
        raw = pd.read_csv(io.BytesIO(_get(url)), thousands=",", low_memory=False)
        raw = raw[raw["Balancing Authority"] == "ERCO"]
        frames.append(
            pd.DataFrame(
                {
                    "ts_utc": pd.to_datetime(raw["UTC Time at End of Hour"], format="%m/%d/%Y %I:%M:%S %p", utc=True),
                    "load_mw": pd.to_numeric(raw["Demand (MW)"], errors="coerce"),
                    "forecast_mw": pd.to_numeric(raw["Demand Forecast (MW)"], errors="coerce"),
                }
            )
        )
    df = pd.concat(frames).dropna(subset=["forecast_mw"]).sort_values("ts_utc").drop_duplicates("ts_utc")
    df = df[df["ts_utc"].dt.tz_convert(TZ).dt.year == YEAR]
    return df.reset_index(drop=True)


def fetch_published_4cp() -> pd.DataFrame:
    docs = json.loads(_get(ERCOT_4CP_LIST))["ListDocsByRptTypeRes"]["DocumentList"]
    doc = next(d["Document"] for d in docs if d["Document"]["FriendlyName"].startswith(f"4CP{YEAR}"))
    sheet = pd.read_excel(io.BytesIO(_get(ERCOT_DOWNLOAD.format(doc["DocID"]))), header=None)
    header_row = sheet.index[sheet.iloc[:, 0].astype(str).str.strip() == "TDSP Code"][0]
    rows = []
    for col in sheet.columns[3:7]:
        label = str(sheet.iloc[header_row, col])
        month, stamp = [s.strip() for s in label.split("\n")]
        end_local = pd.Timestamp(datetime.strptime(stamp, "%m/%d/%Y %H:%M")).tz_localize(TZ)
        rows.append(
            {
                "month": month,
                "interval_end_local": stamp,
                "interval_end_utc": end_local.tz_convert("UTC"),
                "interval_start_utc": (end_local - pd.Timedelta(minutes=15)).tz_convert("UTC"),
                "source_doc": doc["ConstructedName"],
            }
        )
    return pd.DataFrame(rows)


def fetch_weather() -> pd.DataFrame:
    frames = []
    for city, (lat, lon) in CITIES.items():
        hourly = json.loads(_get(OPEN_METEO.format(lat=lat, lon=lon)))["hourly"]
        frames.append(
            pd.DataFrame(
                {"ts_utc": pd.to_datetime(hourly["time"], utc=True), "city": city, "temp_c": hourly["temperature_2m"]}
            )
        )
    return pd.concat(frames).dropna().reset_index(drop=True)


def fetch_dam_spp() -> pd.DataFrame:
    import gridstatus  # optional extra: uv sync --extra ingest

    df = gridstatus.Ercot().get_dam_spp(YEAR)
    df = df[df["Location"].isin(LOAD_ZONES)]
    return pd.DataFrame(
        {
            "ts_utc": pd.to_datetime(df["Interval Start"]).dt.tz_convert("UTC"),
            "location": df["Location"].astype(str),
            "spp_usd_mwh": df["SPP"].astype(float),
        }
    ).reset_index(drop=True)


FETCHERS = {
    "ercot_load": fetch_ercot_load,
    "published_4cp": fetch_published_4cp,
    "weather": fetch_weather,
    "dam_spp": fetch_dam_spp,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def june_slice(name: str, df: pd.DataFrame) -> pd.DataFrame:
    if name == "published_4cp":
        return df[df["month"].str.startswith("June")]
    local = df["ts_utc"].dt.tz_convert(TZ)
    return df[(local.dt.month == 6)]


def refresh() -> dict:
    PARQUET.mkdir(parents=True, exist_ok=True)
    FIXTURE.mkdir(parents=True, exist_ok=True)
    entries = {}
    for name, fetch in FETCHERS.items():
        try:
            df = fetch()
        except Exception as exc:  # prices are optional; the rest are required
            if name in REQUIRED:
                raise
            print(f"skip {name}: {exc}", file=sys.stderr)
            continue
        for kind, folder, frame in (("parquet", PARQUET, df), ("fixture", FIXTURE, june_slice(name, df))):
            path = folder / f"{name}.parquet"
            frame.to_parquet(path, index=False)
            entries[f"{kind}/{name}"] = {
                "path": str(path.relative_to(ROOT)),
                "rows": int(len(frame)),
                "sha256": _sha256(path),
            }
        print(f"{name}: {len(df)} rows")
    manifest = {"year": YEAR, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": entries}
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify(manifest_path: Path = MANIFEST) -> list[str]:
    """Return a list of problems; empty means the cache matches the manifest."""
    if not manifest_path.exists():
        return [f"missing {manifest_path}"]
    manifest = json.loads(manifest_path.read_text())
    problems = []
    for key, entry in manifest["files"].items():
        path = ROOT / entry["path"]
        if not path.exists():
            problems.append(f"{key}: missing {entry['path']}")
            continue
        if _sha256(path) != entry["sha256"]:
            problems.append(f"{key}: sha256 mismatch")
        if len(pd.read_parquet(path)) != entry["rows"]:
            problems.append(f"{key}: row count mismatch")
    for name in REQUIRED:
        for kind in ("parquet", "fixture"):
            if f"{kind}/{name}" not in manifest["files"]:
                problems.append(f"{kind}/{name}: not in manifest")
    return problems


def load(name: str, fixture: bool = False) -> pd.DataFrame:
    """Read one cached table. The demo and calendar only ever read through here."""
    return pd.read_parquet((FIXTURE if fixture else PARQUET) / f"{name}.parquet")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="verify the cache against the manifest; no network")
    args = ap.parse_args(argv)
    if not args.offline:
        refresh()
    problems = verify()
    for p in problems:
        print(p, file=sys.stderr)
    if not problems:
        print(f"cache ok: {MANIFEST.relative_to(ROOT)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
