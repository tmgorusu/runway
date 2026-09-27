# planner
# Runway
Wear leveling for utility-dispatched home batteries. A utility calls the fleet for a megawatt target. Runway chooses which homes deliver it so a hot, contract-worn battery does not take the same energy as a cool one. Backup reserve stays untouched. A call the feasible fleet cannot cover is reported as a shortfall.
The fleet in this plan is synthetic. The ERCOT sample is real public data. There is no Base telemetry here.
## Plan
| File | What it is |
| --- | --- |
| [BUILD_PLAN.md](BUILD_PLAN.md) | Full council plan: decisions, milestones, task cards, cut list, video outline. |
| [tasks.json](tasks.json) | The same task cards as JSON. |
## Specs
| Spec | Who it is for |
| --- | --- |
| [specs/VISION.md](specs/VISION.md) | The merged system. What the demo, the files, and the five-minute video look like when both halves land. |
| [specs/WEAR.md](specs/WEAR.md) | The battery researcher. Heat, aging, the wear scalar, the baselines, the hero ratio, the replay. |
| [specs/MACHINE.md](specs/MACHINE.md) | The other hacker. Cache, calendar, water-fill, workers, dispatcher, benchmark, dashboard, demo. |
Wear and Machine can each hand their spec to a separate model council. The vision spec is the contract those councils are not free to reopen.
## Run it (Machine half)
Clean machine, network off after `uv sync`:
```bash
uv sync
uv run python -m runway.demo --seed 7 --fault offline_wave   # the product: chaos test + artifacts + dashboard
open web/index.html                                          # four views, works from file://
uv run pytest                                                # every test blocks the network
```
| Command | Writes |
| --- | --- |
| `python -m runway.ingest --offline` | verifies `data/parquet/` and `data/fixture/` against `data/manifest.json` |
| `python -m runway.calendar` | `handoff/calls.parquet`, `handoff/hit_flags.json` (needs `CALLING_RULE.md`) |
| `python -m runway.e2e --offline` | `outputs/m1/dispatch.json`, 32 units on the published 6/19/2025 4CP interval |
| `python -m runway.demo --seed 7 --fault offline_wave` | `outputs/track2/metrics.json`, `outputs/demo/decision_table.csv`, `web/index.html` |
| `python -m runway.dispatcher --ladder` | `outputs/track2/ladder.json`, the filmed N |
| `python -m runway.bench` | `outputs/bench/allocate.json` |

Refreshing the cache needs the network: `uv sync --extra ingest && uv run python -m runway.ingest`.
Assumptions, labeled everywhere: reserve fraction 0.30, replacement at health 0.70, fleet of 4,000 synthetic units serving 40 MW (scaled as `40 * N / 4000`). A run on the placeholder cost prints `cost=stub`.
