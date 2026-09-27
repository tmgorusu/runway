"""Build web/index.html from pipeline JSON only. `python -m runway.web`.

The page is one self-contained file: no network, no CDN, opens from file://.
Every number on it comes from a file under outputs/ (or, before Wear's hero lands,
web/fixture/hero.json, labeled FIXTURE on the page). Headline numbers are rendered
into the HTML here; the charts are drawn by inline JS from the same embedded data.

One dashboard: the Austin anomaly map is the main view; live dispatch (thermal fleet, water-fill), physics (heat curve), and the season replay open as popups over it.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from runway import ingest

ROOT = ingest.ROOT
OUT = ROOT / "outputs"
WEB = ROOT / "web"
FIXTURE_HERO = WEB / "fixture" / "hero.json"
SOURCES = {
    "metrics": OUT / "track2" / "metrics.json",
    "run": OUT / "demo" / "run.json",
    "bench": OUT / "bench" / "allocate.json",
    "ladder": OUT / "track2" / "ladder.json",
    "summary": OUT / "replay" / "summary.json",
    "blast": OUT / "physics" / "blast_overlay.json",
    "detail": OUT / "replay" / "detail.json",
    "pair": OUT / "physics" / "reference_pair.json",
    "telemetry_fit": OUT / "physics" / "telemetry_fit.json",
    "hit_flags": ROOT / "handoff" / "hit_flags.json",
    "austin": OUT / "austin" / "events.json",
    "value": OUT / "track1" / "value.json",
    "game_bench": OUT / "bench" / "game.json",
    "basemap": ROOT / "data" / "basemap" / "austin.json",
}
HERO = OUT / "track1" / "hero.json"


def _read(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def heat_curve() -> dict | None:
    """Capacity loss per kWh vs enclosure temperature, from Wear's age() if it imports."""
    try:
        from runway.aging import age
    except ImportError:
        return None
    from runway.fleet import make_fleet

    u = make_fleet(1)[0]
    power_kw, dt_s = 10.0, 90 * 60.0
    kwh = power_kw * dt_s / 3600
    temps = list(range(20, 61, 2))
    return {"power_kw": power_kw, "duration_min": 90, "source": "runway.aging.age (Wear)",
            "points": [[t, age(u, float(t), power_kw, dt_s) / kwh] for t in temps]}


def collect() -> dict:
    data = {k: _read(p) for k, p in SOURCES.items()}
    hero = _read(HERO)
    data["hero"] = hero if hero is not None else _read(FIXTURE_HERO)
    data["hero_is_fixture"] = hero is None
    data["heat"] = heat_curve()
    return data


def _fmt(x, spec=".2f", missing="—"):
    return missing if x is None else format(x, spec)


def headline(data: dict) -> dict[str, str]:
    m = data["metrics"] or {}
    run = data["run"] or {}
    hero = (data["hero"] or {}).get("assumption_sets", {}).get("nominal", {})
    delivered = run.get("delivered_kw")
    return {
        "mw_requested": _fmt(m.get("mw_requested"), ".3f"),
        "mw_delivered": _fmt(None if delivered is None else delivered / 1000, ".3f"),
        "shortfall_mw": _fmt(None if m.get("shortfall_kw") is None else m["shortfall_kw"] / 1000, ".3f"),
        "reserve_violations": str(m.get("reserve_violations", "—")),
        "latency_ms": _fmt(m.get("resole_latency_ms"), ".1f"),
        "n_units": f"{m['n_units']:,}" if isinstance(m.get("n_units"), int) else "—",
        "cost": str(m.get("cost", "—")),
        "seq": f"{m.get('seq_before', '—')} → {m.get('seq_after', '—')}",
        "hero_ratio": _fmt(hero.get("ratio"), ".3f"),
        "hero_label": "FIXTURE (Wear's hero.json not yet written)" if data["hero_is_fixture"] else "outputs/track1/hero.json",
    }


def render(data: dict) -> str:
    h = {k: html.escape(v) for k, v in headline(data).items()}
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    page = TEMPLATE
    for k, v in h.items():
        page = page.replace("{{" + k + "}}", v)
    return page.replace("{{payload}}", payload)


def build() -> Path:
    WEB.mkdir(exist_ok=True)
    out = WEB / "index.html"
    out.write_text(render(collect()))
    return out


TEMPLATE = (Path(__file__).resolve().parent / "dashboard_template.html").read_text().replace(
    "/*GAME_JS*/", (Path(__file__).resolve().parent / "austin_game.js").read_text())


def main(argv=None) -> int:
    out = build()
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
