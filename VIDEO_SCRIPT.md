# Video script (five minutes, seed 7, one recording)

Rules: **synthetic fleet** is on screen the whole time. Every spoken number cites a file in the brackets. Physics lines come only from Wear's `PHYSICS_LINES.md`. Simulated call time and wall-clock latency are always labeled as separate clocks. A run on `cost=stub` never appears in the physics minutes.

Numbers below are from the current stub-cost run. Re-read the cited files before recording, because they change when `marginal_wear` lands.

| Time | Owner | On screen | Spoken (citation) |
| --- | --- | --- | --- |
| 0:00–0:30 | Wear | Two units: same kWh, different enclosure °C, different capacity loss | From `PHYSICS_LINES.md` only. **Pending Wear.** |
| 0:30–1:40 | Wear | 2025 candidate days vs published 4CP; labeled "system-load proxy, score not probability" | Ratio from `outputs/track1/hero.json`. **Off camera until hero.json exists with `policy_for_ratio = even`.** Calendar: all 4 published 2025 4CP intervals (6/19, 7/30, 8/18, 9/04) hit [`handoff/hit_flags.json`] |
| 1:40–2:20 | Machine | `decision_table.csv`, the 2,000-unit saturation row, benchmark | "1,150 units moved on the re-solve: 600 went offline, 549 were added" [`outputs/demo/decision_table.csv`]. "allocate() p50: 1.2 ms at 1,000 units, 13.6 ms at 10,000, 183 ms at 100,000 on an Apple M4" [`outputs/bench/allocate.json`]. Saturation row [`outputs/replay/summary.json`, Wear]. BLAST overlay only if [`outputs/physics/blast_overlay.json`] says success |
| 2:20–3:40 | Machine | Live `python -m runway.demo --seed 7 --fault offline_wave`, then 10 s of the restart log | "4,000 synthetic units, 40 MW requested" [`outputs/track2/metrics.json`]. "The 15% of units holding the largest setpoints go offline" [`metrics.json: fraction_offline`]. "Delivered 40 MW, shortfall 0, reserve violations 0" [`metrics.json`]. "Re-solved in 42 ms of wall clock" [`metrics.json: resole_latency_ms`]. "4,000 is the largest rung under one second: p50 44 ms" [`outputs/track2/ladder.json`]. "Workers are async actors in one process." Restart: "the dispatcher process was SIGKILLed between the journal write and the commit of seq 3; on restart it re-sent seq 3 once, and each seq committed exactly once" [`outputs/track2/restart.log`, `tests/test_dispatcher.py`] |
| 3:40–4:30 | Wear | Replay at the feasible MW, the over-call fixture, the flip flag | [`outputs/replay/summary.json`] **Pending Wear.** |
| 4:30–5:00 | Machine | README command from a clean venv with the network off | "One command. Reserve fraction 0.30, replacement at 0.70, and a 4,000-unit fleet are our assumptions" [`README.md`, `runway/contracts.py`]. Fallback recording [`outputs/demo/fallback_recording.txt`] |

Recording order: record the fault beat now (M8 is green). Keep the fallback transcript on disk. Splice in the hero chart once `outputs/track1/hero.json` lands.
