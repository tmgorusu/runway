# Runway final vision
This is the picture both hackers are building. Wear and Machine each have their own spec. This document is the merged system a judge can run.
Runway assigns a utility megawatt call across a fleet of home batteries so the same delivered energy does more damage to a hot, contract-worn unit than to a cool one, and the assignment avoids that damage. Backup reserve is never used. If the feasible fleet cannot cover the call, the shortfall is reported.
The fleet in the repo is synthetic. The ERCOT dates, load, forecasts, and published 4CP intervals are real. There is no Base telemetry and no claim about how Base actually dispatches.
## 1. What a stranger can do
On a clean machine, with the network off:
```bash
python -m runway.demo --seed 7 --fault offline_wave
```
That command is the product. It reads the cache, dispatches one call, injects the seeded fault, re-solves, and writes the files below. The dashboard and the five-minute video read those files and nothing else.
| File | What it proves |
| --- | --- |
| `outputs/track1/hero.json` | The 2025 wear ratio, computed under even spread |
| `outputs/replay/summary.json` | Three policies, the feasible megawatt level, the saturated fleet, the over-call caveat |
| `outputs/track2/metrics.json` | Delivered megawatts, shortfall, zero reserve violations, re-solve latency |
| `outputs/demo/decision_table.csv` | Which homes moved, and the wear cost of each |
| `outputs/bench/allocate.json` | Allocator time at 1,000, 10,000, and 100,000 units |
| `web/index.html` | Four views bound to those files |
The words **synthetic fleet** are on screen the entire time. Every spoken number cites one of these files or a figure in the hackathon brief.
## 2. Shape of the system
```text
ERCOT 2025 load, forecast, published 4CP          weather (Austin)
        │                                              │
        ▼                                              ▼
   Machine: ingest cache (Parquet, works offline)
        │
        ▼
   Machine: calendar executes CALLING_RULE.md
        │
        ▼
   handoff/calls.parquet  ──────────────►  Wear: hero + replay
        │                                        ▲
        ▼                                        │
   Machine: fleet seed 7  ── initial_health ─────┘  (Wear)
        │
        ▼
   Machine: allocate(policy)
        │     runway  → calls Wear.marginal_wear
        │     even, most_charge → Wear.policies
        ▼
   Machine: dispatcher + one async worker per unit
        │     heartbeat, journal, re-solve, subprocess restart
        ▼
   metrics.json, decision_table.csv, demo, dashboard, video
```
Wear owns every number about heat and aging. Machine owns every process that has to keep running. They meet at the function signatures and the JSON files in this spec.
## 3. Shared contract
Both sides implement these types and no others. `health` is remaining capacity fraction. `age` returns capacity fraction lost over the step. Positive means the battery degraded.
```python
def energy_above_reserve_kwh(u: UnitState) -> float: ...
def step_thermal(u: UnitState, ambient_c: float, power_kw: float, dt_s: float) -> float: ...
def age(u, enclosure_c, power_kw, dt_s, assumption="nominal") -> float: ...
def initial_health(age_years, term_years, assumption="nominal") -> float: ...
def marginal_wear(u, call, power_kw, dt_s, assumption="nominal") -> float: ...
def allocate(call, units, policy, assumption="nominal") -> tuple[list[Setpoint], float]: ...
    # policy is "runway", "even", or "most_charge"
    # second value is shortfall_kw, always >= 0
```
`marginal_wear` is one positive scalar. Higher means it costs more to use that unit at that power. The glide-path weight and the stale-telemetry multiplier are already inside it. At a fixed unit and call, the scalar does not decrease as `power_kw` increases.
`allocate` obeys all of these at once:
- power is inside `[0, p_max_kw]`
- energy drawn stays inside `energy_above_reserve_kwh`
- `sum(power_kw) + shortfall_kw` equals requested kW within `1e-6`
- the same inputs always produce the same setpoints
## 4. The call the system hunts
`CALLING_RULE.md` is committed before any hit-rate file exists. Its sha256 is stored on every output that depends on it.
A calendar day in America/Chicago, in June, July, August, or September 2025, is a candidate when that day's maximum day-ahead ERCOT system forecast is at least 0.97 times the maximum daily day-ahead peak from the first of that month through that day, inclusive. One call per candidate day. The call starts 45 minutes before the forecast peak and lasts 90 minutes. `peak_odds` is that ratio clipped to `[0, 1]`. It is a score, not a probability. The call hits when a published 4CP timestamp falls inside the window.
This is ERCOT system load. It is a proxy for the Austin Energy, GVEC, and CoServ hunts. The screen says so.
## 5. The fleet the judge sees
Seed 7. Base Core hardware: 20 kW, 39.2 kWh, LFP.
| Knob | Demo value | Status |
| --- | --- | --- |
| Reserve fraction | 0.30 | Our assumption, labeled |
| Replacement | health falls below 0.70 | Our assumption, labeled |
| Replay fleet | 4,000 units serving 40 MW | Our assumption, labeled. Gives the allocator room to choose |
| Saturation row | 2,000 units serving 40 MW | Arithmetic minimum at nameplate. Leveling has nothing to move |
| Age and contract | half the units on 10-year terms, half on 12-year; ages spread across the term | Assumption |
| Sun exposure | uniform on `[0, 1]` | Assumption |
| State of charge at each hero call | 1.0 | Assumption, written in `hero.json` |
At full charge and a 0.30 reserve, one unit holds `0.70 * 39.2 = 27.44 kWh` above reserve. Ninety minutes at 20 kW needs 30 kWh. A single unit cannot serve that shape. The replay compares policies only at a megawatt level all three can serve with zero shortfall. The 2,000-unit row is the control: every healthy unit is saturated, and the shortfall belongs to all three policies.
## 6. Policies
All three use the same reserve guard.
- **even.** Same kilowatts to every unit, clipped to power and to energy above reserve, residual filled in `unit_id` order. This policy authors the Track 1 ratio.
- **most_charge.** Fill highest SOC first, `unit_id` breaks ties, up to the same cap.
- **runway.** Water-fill on `marginal_wear`. Machine searches a threshold and, on a 21-point grid of each unit's feasible power, takes the largest power whose scalar is still at or below the threshold.
Glide path, inside the scalar: target health is a straight line from 1.0 at age 0 to 0.70 at `term_years`. Units behind that line cost more. If telemetry is older than 15 minutes and `peak_odds >= 0.9`, the scalar doubles. Both knobs live in the assumption file.
## 7. What the five minutes show
| Time | On screen | File |
| --- | --- | --- |
| 0:00–0:30 | Two units, same kWh, different enclosure temperature, different capacity loss. Reserve untouched. Shortfall reported when the call does not fit. | Wear's reference plot, `summary.json` |
| 0:30–1:40 | Summer 2025 candidate days against the published 4CP intervals. The wear-per-kWh ratio and the miss fraction. System-load proxy labeled. | `hero.json` |
| 1:40–2:20 | Decision table: unit, kW, enclosure °C, marginal wear. The 2,000-unit saturation row. Allocator timings. BLAST overlay if it exists. | `decision_table.csv`, `allocate.json`, `blast_overlay.json` |
| 2:20–3:40 | Live seed-7 offline wave. Requested MW, delivered MW, shortfall, reserve violations at 0, wall-clock re-solve latency. Ten seconds of the subprocess-restart log. | `metrics.json` |
| 3:40–4:30 | Replay at the feasible megawatt level. Over-call case where all three policies hit the replacement line. The assumption-set flip flag, read as whatever it is. | `summary.json` |
| 4:30–5:00 | The one command, from a clean environment, network off. Assumptions labeled. | README |
Simulated call time and wall-clock latency are labeled as different clocks.
## 8. Invariants the whole repo enforces
1. No setpoint draws backup reserve. A test fails when one does.
2. Shortfall is reported and left uncovered.
3. `hero.json` ratio is computed with `policy_for_ratio = "even"`.
4. A ratio near 1.0 ships. The calling rule stays at 0.97.
5. Early-replacement counts are compared only on a megawatt level that all three policies can serve.
6. The over-call fixture shows early replacements for runway, even spread, and most-charge-first.
7. The demo entrypoint is the chaos test for seed 7.
8. The hosted prototype URL and the prototype's replacement counts are absent from the repo.
9. A run that still uses the stub cost prints `cost=stub` and does not appear in the video's physics minutes.
## 9. Tracks the finished build enters
Track 1, Open Grid Data: the wear-per-kWh ratio on real 2025 candidate days versus published 4CP intervals.
Track 2, Orchestration: the seeded offline wave, the re-solve, the journal, and the subprocess restart.
Track 3 is out.
## 10. Out of scope
Price forecasting, a trading bot, a mobile app, an HTTP API, El Paso, a CoServ hardware model, a GVEC fleet, 100,000 live workers, a second aging formula, and a live on-camera kill of the dispatcher.
## 11. Done
The vision is met when a clean-venv run of the demo command, network disabled, writes the six artifacts in section 1, the invariant tests pass, and the video cites only those artifacts and the brief. Wear's spec and Machine's spec are the two halves of that bar.