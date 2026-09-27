"""Build web/index.html from pipeline JSON only. `python -m runway.web`.

The page is one self-contained file: no network, no CDN, opens from file://.
Every number on it comes from a file under outputs/ (or, before Wear's hero lands,
web/fixture/hero.json, labeled FIXTURE on the page). Headline numbers are rendered
into the HTML here; the charts are drawn by inline JS from the same embedded data.

Four views: thermal fleet, water-fill, heat curve, replay.
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
        "n_units": str(m.get("n_units", "—")),
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


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Runway Dispatch</title>
<style>
:root{--bg:#f7f6f2;--panel:#fff;--ink:#1c1c1a;--muted:#6b6a64;--line:#e3e1da;--accent:#c2410c;--cool:#2563eb;--ok:#15803d;--off:#9ca3af;--warn:#b91c1c}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141412;--panel:#1d1d1a;--ink:#ecebe6;--muted:#9c9a92;--line:#2f2e2a;--accent:#fb923c;--cool:#60a5fa;--ok:#4ade80;--off:#57534e;--warn:#f87171}}
:root[data-theme="dark"]{--bg:#141412;--panel:#1d1d1a;--ink:#ecebe6;--muted:#9c9a92;--line:#2f2e2a;--accent:#fb923c;--cool:#60a5fa;--ok:#4ade80;--off:#57534e;--warn:#f87171}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.banner{position:sticky;top:0;z-index:5;background:var(--accent);color:#fff;padding:6px 16px;font-weight:600;letter-spacing:.02em}
.banner small{font-weight:400;opacity:.9;margin-left:8px}
main{max-width:1200px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:8px 0 2px}
h2{font-size:15px;margin:0 0 8px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.sub{color:var(--muted);margin:0 0 16px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-bottom:16px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.stat b{display:block;font-size:22px;font-variant-numeric:tabular-nums}
.stat span{color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:12px}
section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;min-width:0}
canvas,svg{width:100%;display:block}
.legend{display:flex;flex-wrap:wrap;gap:12px;color:var(--muted);font-size:12px;margin-top:6px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.pending{color:var(--muted);border:1px dashed var(--line);border-radius:6px;padding:24px;text-align:center}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:13px}
td,th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:right}
td:first-child,th:first-child{text-align:left}
.label{font-size:12px;color:var(--muted)}
.fixture{color:var(--warn);font-weight:600}
</style>
</head>
<body>
<div class="banner">synthetic fleet<small>Seed-7 synthetic Base Core units (20 kW, 39.2 kWh). ERCOT 2025 dates and published 4CP intervals are real. No Base telemetry.</small></div>
<main>
<h1>Runway: seed-7 offline wave</h1>
<p class="sub">Call progress uses simulated time. Re-solve latency uses the wall clock. Cost scalar: <b>{{cost}}</b>. N = {{n_units}}.</p>
<div class="stats">
  <div class="stat"><span>Requested MW</span><b id="mw-req">{{mw_requested}}</b></div>
  <div class="stat"><span>Delivered MW after re-solve</span><b id="mw-del">{{mw_delivered}}</b></div>
  <div class="stat"><span>Shortfall MW (never covered from reserve)</span><b id="short">{{shortfall_mw}}</b></div>
  <div class="stat"><span>Reserve violations</span><b id="rv">{{reserve_violations}}</b></div>
  <div class="stat"><span>Re-solve latency, wall clock (ms)</span><b id="lat">{{latency_ms}}</b></div>
  <div class="stat"><span>seq</span><b>{{seq}}</b></div>
</div>
<div class="grid">
  <section id="view-thermal">
    <h2>Thermal fleet</h2>
    <canvas id="fleet" height="260"></canvas>
    <div class="legend" id="fleet-legend"></div>
  </section>
  <section id="view-waterfill">
    <h2>Water-fill</h2>
    <svg id="wf" viewBox="0 0 600 260" preserveAspectRatio="none" height="260"></svg>
    <div class="legend"><span><i class="sw" style="background:var(--cool)"></i>after re-solve (kW)</span><span><i class="sw" style="background:var(--off)"></i>offline</span><span>x: units sorted by cost scalar, cheapest left</span></div>
  </section>
  <section id="view-heat">
    <h2>Heat curve</h2>
    <div id="heat"></div>
  </section>
  <section id="view-replay">
    <h2>Replay</h2>
    <p class="label">Wear per kWh, misses ÷ hits, even spread (nominal): <b id="hero-ratio" style="font-size:20px">{{hero_ratio}}</b><br>
    Source: <span id="hero-src">{{hero_label}}</span></p>
    <div id="replay"></div>
    <h2 style="margin-top:14px">Allocator benchmark (p50, allocate() only)</h2>
    <div id="bench"></div>
  </section>
</div>
</main>
<script id="payload" type="application/json">{{payload}}</script>
<script>
(function(){
const D = JSON.parse(document.getElementById('payload').textContent);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const run = D.run;

// Thermal fleet ------------------------------------------------------------
(function(){
  const c = document.getElementById('fleet'), leg = document.getElementById('fleet-legend');
  if(!run){ c.replaceWith(pending('outputs/demo/run.json not written yet: run the demo')); return; }
  const dpr = window.devicePixelRatio||1, W = c.clientWidth, H = 260;
  c.width = W*dpr; c.height = H*dpr; const g = c.getContext('2d'); g.scale(dpr,dpr);
  const n = run.units.length, cols = Math.ceil(Math.sqrt(n*W/H)), rows = Math.ceil(n/cols);
  const s = Math.min(W/cols, H/rows), off = new Set(run.faulted);
  const after = new Map(run.setpoints_after.map(([u,p])=>[u,p]));
  run.units.forEach(([id,sun],i)=>{
    const x = (i%cols)*s, y = Math.floor(i/cols)*s;
    if(off.has(id)) g.fillStyle = css('--off');
    else { const t = sun; g.fillStyle = `rgb(${Math.round(60+180*t)},${Math.round(110-40*t)},${Math.round(235-200*t)})`; }
    g.fillRect(x,y,Math.max(1,s-0.5),Math.max(1,s-0.5));
    if(!off.has(id) && (after.get(id)||0)>0 && s>=4){ g.fillStyle='rgba(255,255,255,.85)'; g.fillRect(x+s/2-1,y+s/2-1,2,2); }
  });
  const heatNote = D.heat ? '' : ' (enclosure °C pending Wear’s thermal.py; colored by sun exposure)';
  leg.innerHTML = `<span><i class="sw" style="background:rgb(60,110,235)"></i>cool</span><span><i class="sw" style="background:rgb(240,70,35)"></i>sun-baked</span><span><i class="sw" style="background:${css('--off')}"></i>offline (seed-7 wave)</span><span>dot: dispatched after re-solve</span><span>${heatNote}</span>`;
})();

// Water-fill ---------------------------------------------------------------
(function(){
  const svg = document.getElementById('wf');
  if(!run){ svg.replaceWith(pending('run.json missing')); return; }
  const off = new Set(run.faulted), after = new Map(run.setpoints_after.map(([u,p])=>[u,p]));
  const pts = run.units.map(([id,sun,h,cost])=>({id,cost,p:after.get(id)||0,off:off.has(id)})).sort((a,b)=>a.cost-b.cost||a.id-b.id);
  const W=600,H=260,pad=24, n=pts.length, bw=(W-pad)/n, pmax=20;
  let out = `<line x1="${pad}" y1="${H-pad}" x2="${W}" y2="${H-pad}" stroke="${css('--line')}"/>`;
  out += `<text x="2" y="12" font-size="10" fill="${css('--muted')}">20 kW</text>`;
  const step = Math.max(1, Math.floor(n/600));
  for(let i=0;i<n;i+=step){ const q=pts[i], h=(q.off?pmax:q.p)/pmax*(H-2*pad);
    out += `<rect x="${pad+i*bw}" y="${H-pad-h}" width="${Math.max(bw*step,0.6)}" height="${h}" fill="${q.off?css('--off'):css('--cool')}" opacity="${q.off?0.5:1}"/>`; }
  const used = pts.filter(q=>!q.off && q.p>0); const lam = used.length ? Math.max(...used.map(q=>q.cost)) : null;
  if(lam!==null){ const idx = pts.findIndex(q=>q.cost>lam); const x = pad + (idx<0?n:idx)*bw;
    out += `<line x1="${x}" y1="${pad}" x2="${x}" y2="${H-pad}" stroke="${css('--accent')}" stroke-dasharray="4 3"/><text x="${x+4}" y="${pad+10}" font-size="11" fill="${css('--accent')}">threshold λ = ${lam.toFixed(3)}</text>`; }
  svg.innerHTML = out;
})();

// Heat curve ---------------------------------------------------------------
(function(){
  const el = document.getElementById('heat');
  if(!D.heat){ el.appendChild(pending('Waiting on Wear: runway.aging.age is not importable yet. No temperature model is drawn until it is.')); return; }
  const P = D.heat.points, W=600,H=240,pad=34;
  const xs=P.map(p=>p[0]), ys=P.map(p=>p[1]), x0=Math.min(...xs),x1=Math.max(...xs),y1=Math.max(...ys)||1;
  const X=t=>pad+(t-x0)/(x1-x0)*(W-pad-8), Y=v=>H-pad-v/y1*(H-2*pad);
  const path = P.map((p,i)=>(i?'L':'M')+X(p[0]).toFixed(1)+','+Y(p[1]).toFixed(1)).join('');
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" height="240"><path d="${path}" fill="none" stroke="${css('--accent')}" stroke-width="2"/>
  <line x1="${pad}" y1="${H-pad}" x2="${W}" y2="${H-pad}" stroke="${css('--line')}"/><text x="${pad}" y="${H-8}" font-size="11" fill="${css('--muted')}">${x0} °C</text><text x="${W-40}" y="${H-8}" font-size="11" fill="${css('--muted')}">${x1} °C</text>
  <text x="${pad}" y="14" font-size="11" fill="${css('--muted')}">capacity fraction lost per kWh (${D.heat.power_kw} kW, ${D.heat.duration_min} min)</text></svg>
  <p class="label">Source: ${D.heat.source}</p>`;
})();

// Replay + bench -----------------------------------------------------------
(function(){
  const el = document.getElementById('replay');
  if(D.hero_is_fixture){ document.getElementById('hero-src').className='fixture'; }
  const S = D.summary;
  if(!S){ el.appendChild(pending('outputs/replay/summary.json not written yet (Wear).')); }
  else {
    const pol=['runway','even','most_charge'];
    el.innerHTML = `<table><tr><th>policy</th><th>early replacements @ ${S.feasible_mw} MW</th><th>reserve violations</th><th>2,000-unit shortfall kW</th></tr>`+
      pol.map(p=>`<tr><td>${p}</td><td>${S.early_replacements[p]}</td><td>${S.reserve_violations[p]}</td><td>${S.saturated_2000_shortfall_kw[p]}</td></tr>`).join('')+
      `</table><p class="label">Over-call fixture fails all three policies: ${S.over_call_all_policies_fail}. Ranking flipped across assumption sets: ${S.ranking_flipped_across_assumption_sets}.</p>`;
  }
  const b = document.getElementById('bench');
  if(!D.bench){ b.appendChild(pending('outputs/bench/allocate.json not written yet')); return; }
  b.innerHTML = `<table><tr><th>units</th><th>p50 ms</th></tr>`+Object.entries(D.bench.sizes).map(([n,r])=>`<tr><td>${(+n).toLocaleString()}</td><td>${r.p50_ms.toFixed(2)}</td></tr>`).join('')+`</table><p class="label">${D.bench.cpu}; cost=${D.bench.cost}</p>`;
})();

function pending(msg){ const d=document.createElement('div'); d.className='pending'; d.textContent=msg; return d; }
})();
</script>
</body>
</html>
"""


def main(argv=None) -> int:
    out = build()
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
