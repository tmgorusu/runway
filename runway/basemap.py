"""Offline Austin basemap for the dashboard, from U.S. Census Bureau TIGER/Line 2024 (public domain).

`python -m runway.basemap --fetch` downloads the TIGER files (network, once) and writes
data/basemap/austin.json: Austin city limits, large water bodies, and primary and secondary
roads inside the dashboard's map area, simplified to about 40 m. The dashboard only reads
the JSON; nothing is fetched at runtime.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

from runway.paths import DATA

OUT = DATA / "basemap" / "austin.json"
TIGER = "https://www2.census.gov/geo/tiger/TIGER2024/{kind}/tl_2024_{code}_{suffix}.zip"
COUNTIES = ("48453", "48491", "48209")  # Travis, Williamson, Hays
BBOX = (29.98, 30.64, -98.16, -97.40)  # lat0, lat1, lon0, lon1 (covers every feeder zone with margin)
TOLERANCE_DEG = 0.0004
MIN_WATER_M2 = 150_000
LABELS = {  # TIGER FULLNAME -> short label shown on the map
    "I- 35": "I-35", "Loop 1": "MoPac", "US Hwy 183": "US 183", "State Hwy 71": "SH 71", "State Hwy 130": "SH 130",
    "Loop 360": "Loop 360", "US Hwy 290": "US 290", "State Hwy 45": "SH 45",
}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "runway-hackathon/0.1"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def _reader(raw: bytes):
    import shapefile  # pyshp; only needed for --fetch
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = z.namelist()
    part = lambda ext: io.BytesIO(z.read(next(n for n in names if n.endswith(ext))))
    return shapefile.Reader(shp=part(".shp"), shx=part(".shx"), dbf=part(".dbf"))


def simplify(points: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker on lon/lat points."""
    if len(points) < 3:
        return points
    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = points[b] - points[a]
        rel = points[a + 1:b] - points[a]
        norm = np.hypot(*seg)
        d = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / norm if norm > 0 else np.hypot(rel[:, 0], rel[:, 1])
        i = int(np.argmax(d))
        if d[i] > tol:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return points[keep]


def _inside(p) -> np.ndarray:
    return (p[:, 1] >= BBOX[0]) & (p[:, 1] <= BBOX[1]) & (p[:, 0] >= BBOX[2]) & (p[:, 0] <= BBOX[3])


def _parts(shape):
    pts = np.asarray(shape.points, dtype=float)
    bounds = list(shape.parts) + [len(pts)]
    return [pts[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1)]


def _round(p) -> list:
    return [[round(float(x), 4), round(float(y), 4)] for x, y in p]


def build(fetch=_get) -> dict:
    roads, labels_pts, water, city = [], {}, [], []
    for code in COUNTIES:
        r = _reader(fetch(TIGER.format(kind="ROADS", code=code, suffix="roads")))
        for rec, shp in zip(r.iterRecords(), r.iterShapes()):
            if rec["MTFCC"] not in ("S1100", "S1200"):
                continue
            for part in _parts(shp):
                m = _inside(part)
                if not m.any():
                    continue
                line = simplify(part, TOLERANCE_DEG)
                if len(line) < 2:
                    continue
                roads.append({"c": 1 if rec["MTFCC"] == "S1100" else 2, "p": _round(line)})
                name = LABELS.get(rec["FULLNAME"])
                if name:
                    labels_pts.setdefault(name, []).extend(part[m].tolist())
        w = _reader(fetch(TIGER.format(kind="AREAWATER", code=code, suffix="areawater")))
        for rec, shp in zip(w.iterRecords(), w.iterShapes()):
            if rec["AWATER"] < MIN_WATER_M2:
                continue
            rings = [simplify(p, TOLERANCE_DEG / 2) for p in _parts(shp) if _inside(p).any()]
            water += [_round(p) for p in rings if len(p) >= 3]
    places = _reader(fetch(TIGER.format(kind="PLACE", code="48", suffix="place")))
    for rec, shp in zip(places.iterRecords(), places.iterShapes()):
        if rec["NAME"] == "Austin":
            city += [_round(simplify(p, TOLERANCE_DEG)) for p in _parts(shp) if len(p) >= 3]
    labels = []
    for name, pts in sorted(labels_pts.items()):
        pts = np.asarray(pts)
        mid = pts[np.argsort(np.hypot(pts[:, 0] + 97.7431, pts[:, 1] - 30.2672))[len(pts) // 3]]
        labels.append({"name": name, "lon": round(float(mid[0]), 4), "lat": round(float(mid[1]), 4)})
    out = {"source": "U.S. Census Bureau TIGER/Line 2024 (public domain): roads, area water, places",
           "bbox": BBOX, "city": city, "water": water, "roads": roads, "labels": labels}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", action="store_true", help="download TIGER/Line files (network) and rebuild the basemap")
    ap.add_argument("--from-dir", type=Path, help="build from TIGER zips already downloaded into this folder")
    a = ap.parse_args(argv)
    if a.from_dir:
        out = build(lambda url: (a.from_dir / url.rsplit("/", 1)[1]).read_bytes())
    elif a.fetch:
        out = build()
    else:
        print("basemap present" if OUT.exists() else "no basemap; run with --fetch")
        return 0
    print(f"basemap: {len(out['roads'])} road lines, {len(out['water'])} water rings, {len(out['city'])} city rings, "
          f"{OUT.stat().st_size / 1024:.0f} KB -> {OUT.relative_to(DATA.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
