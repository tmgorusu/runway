# Runway

[![tests](https://github.com/tmgorusu/runway/actions/workflows/tests.yml/badge.svg)](https://github.com/tmgorusu/runway/actions/workflows/tests.yml)

**Wear-aware dispatch for utility-called home batteries.** When ERCOT's grid misbehaves in the Austin Energy territory (a likely peak interval, a price spike, a heat anomaly), Runway decides whether a call is worth the battery life it costs. It then picks which feeders and which batteries should answer, without overloading a feeder or touching backup reserve.

![Runway dashboard: the Austin anomaly map with targeted feeder zones for the July 30, 2025 4CP call](docs/img/dashboard.png)

> **Real vs synthetic.** ERCOT 2025 load, day-ahead forecasts, published 4CP intervals, LZ_AEN day-ahead prices, Austin weather, and the Austin basemap are real public data. The battery fleet, its telemetry, and the feeder zones are **synthetic**, sized like Base Core units (20 kW, 39.2 kWh, LFP). There is no Base Power data here, and nothing claims how Base Power or Austin Energy operates.

## Quick start

Python 3.12+ and [uv](https://docs.astral.sh/uv/) (or plain pip). No API keys; the network is only used to install.

```bash
git clone https://github.com/tmgorusu/runway && cd runway
uv sync                                                      # or: python -m venv .venv && .venv/bin/pip install -r requirements.txt
uv run python -m runway.demo --seed 7 --fault offline_wave   # the product: about 1 minute the first time, seconds after
open web/index.html                                          # the dashboard (any browser, works offline from file://)
uv run pytest                                                # 129 tests, every one with the network blocked
```

## How to reproduce the demo

**Environment variables and API keys: none.** Every source is public and keyless, and the repo ships a verified offline cache. [`.env.example`](.env.example) exists only to document that; the code reads no environment variables.

1. Run `uv sync` once, with the network on.
2. Optionally turn the network off.
3. Run `uv run python -m runway.demo --seed 7 --fault offline_wave`. It:
   - verifies the cache in `data/` against `data/manifest.json`;
   - executes the calling rule;
   - dispatches 40 MW across 4,000 synthetic units and knocks the 15% holding the most power offline mid-call;
   - re-solves, then rebuilds every artifact and `web/index.html`.

   The console prints requested and delivered MW, shortfall, `reserve_violations = 0`, and the wall-clock re-solve latency (about 90 ms on an Apple M2). The Wear artifacts (hero ratio, replay, BLAST overlay, telemetry fit, Austin anomalies) rebuild only when their inputs change.
4. Open `web/index.html`. July 30, 2025 is the default event; use the timeline or ← → to change it, click a zone to take its feeder out, and open the popups (Value, Controls, Compare, Shapley, Event day, Live dispatch, Season, Physics, How it works).
5. To prove it runs offline, CI clones the repo on Linux, runs `uv sync`, then runs the full test suite and the demo with every socket blocked (`.github/workflows/tests.yml`).

Individual steps, if you want them:

| Command | Writes |
|---|---|
| `python -m runway.wear` | `outputs/track1/{hero,value}.json`, `outputs/replay/*.json`, `outputs/physics/*.json`, `outputs/telemetry/fleet_estimates.parquet`, `outputs/austin/events.json`, `outputs/bench/game.json`, `PHYSICS_LINES.md` |
| `python -m runway.austin --plan 2025-07-30 --mw 40` | `outputs/austin/plan_2025-07-30.csv`, per-unit setpoints for one anomaly day (add `--out-zone F0305` to take a feeder out) |
| `python -m runway.austin --bench` | `outputs/bench/game.json`, the game timed at 4,000 / 10,000 / 100,000 units |
| `python -m runway.calendar` | `handoff/calls.parquet`, `handoff/hit_flags.json` from `CALLING_RULE.md` |
| `python -m runway.dispatcher --ladder` | `outputs/track2/ladder.json`, the largest fleet (4,000 / 2,000 / 500) that re-solves in under a second |
| `python -m runway.bench` | `outputs/bench/allocate.json`, allocator time at 1,000 / 10,000 / 100,000 units |
| `python -m runway.ingest --offline` | verifies `data/` against `data/manifest.json` |

**Rebuilding the data from the network** (optional; still no keys):

- **ERCOT and weather:** `uv sync --extra ingest && uv run python -m runway.ingest`.
- **Synthetic telemetry:** `python -m runway.telemetry` regenerates it deterministically.
- **Basemap:** `python -m runway.basemap --fetch` (needs `pip install pyshp`).
- **BLAST-Lite reference:** `physics/make_blast_reference.py` needs a separate Python ≤ 3.12 environment with `blast-lite`, because BLAST-Lite requires `numpy<2`. The runway package never imports BLAST-Lite.

## Tech stack and architecture

**Stack:** Python 3.12, numpy, pandas, pyarrow (Parquet cache), asyncio (dispatcher and workers), scipy (only for the SLSQP optimality test), pytest, uv, and GitHub Actions. The dashboard is one self-contained HTML file with vanilla JavaScript and canvas, no frameworks or CDNs. The game solver is shared JavaScript, and Node checks it against the Python solver in CI.

```text
 REAL PUBLIC DATA (cached in data/, verified by manifest)          SYNTHETIC (labeled everywhere)
 ERCOT load + day-ahead forecast (EIA-930)                          4,000-unit fleet registry
 published 4CP intervals (ERCOT NP9-83-M)                           daily unit telemetry (Apr–Sep 2025)
 LZ_AEN day-ahead prices (ERCOT NP4-180-ER)                         feeder zones + hosting limits
 Austin weather (Open-Meteo)  ·  basemap (TIGER/Line)
        │                                                                  │
        ▼                                                                  ▼
 calendar ── CALLING_RULE.md (0.97, sha256 on every call)        telemetry_fit ── unit state:
        │                                                          exposure, health, cycles, heartbeats
        ▼                                                                  │
 anomaly detector (4CP candidate · LZ_AEN spike · heat) ◄──────────────────┘
        │
        ▼
 Wear physics: step_thermal ─► age (BLAST-Lite form, Arrhenius) ─► marginal_wear = d/dP wear
        │
        ├─► subgrid game: Stackelberg price ─► coalition search ─► Shapley credit   (Python + browser JS)
        ├─► allocate(): water-fill on marginal_wear · even · most_charge   (same reserve guard)
        └─► dispatcher: async unit workers, heartbeats, write-ahead journal, re-solve, SIGKILL restart
        │
        ▼
 outputs/*.json  ─►  web/index.html  (one offline dashboard: Austin map + popups)
```

| Piece | Where | What it does |
|---|---|---|
| Thermal model | `runway/thermal.py` | Lumped enclosure model with ambient exchange, solar gain × exposure, and resistive heat, integrated exactly |
| Aging model | `runway/aging.py` | BLAST-Lite large-format LFP calendar and cycle fade, Arrhenius cycle term (Wang 2011), C-rate term; three labeled assumption sets in `assumptions/` |
| Marginal wear | `runway/marginal.py` | Forward difference of call wear in power, nondecreasing by construction; vectorized grid for 100,000 units |
| Subgrid game | `runway/austin.py`, `runway/austin_game.js` | Clearing price with hosting limits, activation-cost coalition search, Shapley credit; unit-level realization and plan export |
| Allocator | `runway/allocate.py` | 21-point water-fill; delivered kW + shortfall = request; never touches reserve |
| Dispatcher | `runway/dispatcher.py`, `worker.py`, `journal.py` | asyncio actors, heartbeat timeouts, write-ahead journal, re-solve on missed heartbeats, restart applies each setpoint once |
| Telemetry | `runway/telemetry.py`, `telemetry_fit.py` | Synthetic Base-like telemetry and the estimator that turns it into unit state (validated against hidden truth) |
| Dashboard | `runway/web.py`, `runway/dashboard_template.html` | One page, built only from pipeline JSON |

## Results

Every number comes from a file the one command writes.

| | Result | Source |
|---|---|---|
| **2025 hunt** | A 0.97 day-ahead rule, committed before any hit file existed, flags 52 candidate days and catches all 4 published 2025 4CP intervals. | `handoff/hit_flags.json` |
| **Value** | About \$2.7M at stake on a 40 MW contract (the brief's rough \$17/kW per interval). The 52 calls spent 3.21 battery-equivalents of capacity, 91.9% of it on calls that missed. In hindsight on 2025, a 0.98 threshold catches the same 4 with 38 calls and 27% less wear. | `outputs/track1/value.json` |
| **Routing** | The same 15 kWh costs a sun-baked unit 1.27× the capacity of a shaded one. Marginal-wear routing uses 1.4% less capacity over the summer than even spread, and 3.4% less over the call windows. | `outputs/physics/reference_pair.json`, `outputs/replay/detail.json` |
| **Austin anomalies** | 58 anomaly days. On July 30 the game targets 22 of 30 zones (3,523 batteries) with 0 feeder overloads; even spread overloads 7. Staying within feeder limits costs 5.5% more wear. | `outputs/austin/events.json` |
| **Orchestration** | 4,000 async workers, 15% knocked offline: re-solved in about 90 ms with 40.000 MW delivered, 0 shortfall, and 0 reserve violations. A SIGKILLed dispatcher restarts and applies each setpoint once. | `outputs/track2/metrics.json`, `outputs/track2/restart.log` |
| **Speed** | `allocate()`: 9 ms at 1,000 units, 79 ms at 10,000, 835 ms at 100,000. The subgrid game: about 240 ms at any fleet size (30 zone curves); 1.75 s end to end at 100,000 units. | `outputs/bench/allocate.json`, `outputs/bench/game.json` |
| **Physics check** | The aging model matches NREL BLAST-Lite within 0.2% (calendar, 25 and 45 °C) and 1.4% (cycle, 25 °C), with zero fitted parameters. Fitted thermal exposure tracks the hidden truth at 0.996; the installer's sun survey alone gets 0.50. | `outputs/physics/blast_overlay.json`, `outputs/physics/telemetry_fit.json` |

## Datasets, synthetic data, and provenance

| Dataset | Real or synthetic | Source and license | File in repo | How it was made |
|---|---|---|---|---|
| ERCOT system load and day-ahead forecast, 2025 hourly | Real | U.S. EIA Hourly Electric Grid Monitor (EIA-930), public domain | `data/parquet/ercot_load.parquet` | `runway/ingest.py`; sha256 in `data/manifest.json` |
| Published 2025 4CP intervals | Real | ERCOT report NP9-83-M, Four Coincident Peak Calculations (public) | `data/parquet/published_4cp.parquet` | `runway/ingest.py` |
| Load-zone day-ahead prices incl. LZ_AEN, 2025 hourly | Real | ERCOT NP4-180-ER via gridstatus (public) | `data/parquet/dam_spp.parquet` | `runway/ingest.py`; used by the anomaly detector, never by the allocator |
| Austin, Seguin, Denton hourly temperature, 2025 | Real | Open-Meteo historical archive (ERA5), CC BY 4.0 | `data/parquet/weather.parquet` | `runway/ingest.py` |
| Austin basemap (roads, water, city limits) | Real | U.S. Census Bureau TIGER/Line 2024, public domain | `data/basemap/austin.json` | `runway/basemap.py`, simplified to about 40 m |
| BLAST-Lite reference aging curves | Real model output | NREL BLAST-Lite 1.1.1, Apache-2.0 | `physics/blast_lite_reference.csv` | `physics/make_blast_reference.py`, run once |
| Battery fleet registry (4,000 units) | **Synthetic** | Generated; seed 7 | `data/telemetry/registry.parquet` | `runway/telemetry.py`: install dates, terms, mounting, shade, ZIP, lat/lon |
| Daily unit telemetry, Apr–Sep 2025 | **Synthetic** | Generated, driven by the real Austin weather and the real 2025 call days | `data/telemetry/daily.parquet` | Wear's thermal and aging models with per-unit scatter, sensor noise, and heartbeat outages |
| Hidden truth for validation | **Synthetic** | Generated | `data/telemetry/truth.parquet` | Read only by the validation in `runway/telemetry_fit.py` |
| Feeder zones and hosting limits | **Synthetic** | Generated; seed 7 | inside `outputs/austin/events.json` | 0.06° grid cells over the fleet; hosting limits 35–80% of nameplate |
| Assumption sets (reserve 0.30, replacement at 0.70, thermal and aging constants) | Mixed | Each number labeled *sourced* or *assumption* | `assumptions/*.json` | Wear council; see `docs/BUILD_PLAN.md` |

## Known limitations

- **Synthetic fleet, telemetry, and feeders.** The telemetry is generated with our own physics models, so the telemetry fit tests the estimator, not the physics. The feeder zones are grid cells, not Austin Energy's feeder map.
- **One physics parameter carries the routing result.** How much faster cycling wears a hot cell drives the hot/cool difference. With BLAST-Lite's small-cell LFP data, which shows no such effect, the ratio is 1.000 and the routing gain disappears. BLAST-Lite's large-format model even reverses the sign, which its own documentation calls a fit artifact.
- **Hindsight on one year.** The 0.98 result is 2025 only; the committed rule stays 0.97.
- **ERCOT system load is a proxy.** It stands in for each utility's own load in the 4CP intervals. `peak_odds` is a score, not a probability.
- **Feeder limits cost wear.** Respecting them costs about 5.5% more wear than even spread on July 30, which overloads 7 feeders.
- **Saturated fleets.** A 2,000-unit fleet asked for 40 MW falls 5.8 MW short under every policy, and called daily for ten years every policy loses the same 236 units. Leveling can't create megawatts.
- **Browser model is approximate.** The dashboard re-solves on 40-point zone supply curves; the unit-level Python run is the reference.

## Next steps

1. Replace the synthetic registry and telemetry with a real fleet export in the same schema (`data/telemetry/*.parquet`); the estimator and game run unchanged.
2. Tune the calling rule on several years of ERCOT data, and price the call-count trade-off with the operator's own cost per battery-equivalent (the Value popup already accepts it).
3. Use each utility's own 4CP-interval load and real feeder hosting capacity in place of the proxies.
4. Measure the cycle-aging temperature dependence on the actual cells, since the routing gain depends on it.
5. Add the 12CP counterfactual for ERCOT's proposed rule change.

## Repository map

| Path | What it is |
|---|---|
| `runway/` | The package. Machine: `contracts`, `fleet`, `ingest`, `calendar`, `allocate`, `worker`, `dispatcher`, `journal`, `demo`, `web`. Wear: `thermal`, `aging`, `marginal`, `policies`, `hero`, `replay`, `overlay`, `telemetry`, `telemetry_fit`, `austin`, `value`, `basemap` |
| `data/` | Offline cache: ERCOT, prices, and weather Parquet with a manifest; synthetic telemetry; basemap |
| `outputs/` | Every artifact the dashboard and video cite |
| `web/index.html` | The dashboard |
| `CALLING_RULE.md` | The pre-registered calling rule; its sha256 is on every call |
| `PHYSICS_LINES.md`, `VIDEO_SCRIPT.md` | Generated narration, every number with its source file |
| `physics/` | BLAST-Lite reference run and its script |
| `docs/` | Merged spec (`VISION.md`), half-specs (`WEAR.md`, `MACHINE.md`), build plan with council record, task cards, screenshots |
| `.github/workflows/tests.yml` | CI: tests and demo with the network blocked |

## Credits

EIA-930 (U.S. Energy Information Administration) · ERCOT MIS reports · [Open-Meteo](https://open-meteo.com/) (CC BY 4.0) · U.S. Census Bureau TIGER/Line · NREL [BLAST-Lite](https://github.com/NREL/BLAST-Lite) (Apache-2.0; reference run only, no code redistributed) · J. Wang et al., "Cycle-life model for graphite-LiFePO4 cells," *J. Power Sources* 196 (2011) 3942–3948.

© 2026 the Runway authors. All rights reserved. No license is granted.
