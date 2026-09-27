# Machine specification
Owner: the hacker building the running system.
Machine produces the cache, the calendar, the runway water-fill, the workers, the dispatcher, the benchmark, the dashboard, and the one-command demo. Wear produces temperature, aging, the wear scalar, the baselines, the hero ratio, and the replay. The merged picture is [VISION.md](VISION.md). This file is the half Machine can build and test alone.
## 1. Responsibility
Machine implements and owns:
- `runway/contracts.py`
- `runway/fleet.py`
- `runway/ingest.py`
- `runway/calendar.py`
- `runway/allocate.py` (the `runway` policy)
- `runway/stub_cost.py`
- `runway/worker.py`
- `runway/dispatcher.py`
- `runway/demo.py` and `python -m runway.e2e`
- `web/index.html`
- `data/` including the offline fixture
- `VIDEO_SCRIPT.md`
- tests named in section 6
Machine does not edit `thermal.py`, `aging.py`, `marginal.py`, `policies.py`, or `CALLING_RULE.md`.
## 2. Decisions Machine may make
- Journal format and heartbeat timeout.
- Async structure of workers inside one process.
- How many points the water-fill evaluates between 0 and each unit's feasible power. The default is 21.
- Layout inside the four dashboard views.
- Which fleet size is filmed, chosen from the ladder in section 4, once re-solve latency is measured.
## 3. Decisions already closed
- The live fault is seed 7. The 15% of units with the largest setpoints go offline. The dispatcher re-solves. The screen shows requested MW, delivered MW, shortfall, `reserve_violations = 0`, and wall-clock `resole_latency_ms`.
- Dispatcher restart is a real subprocess kill inside pytest. It is scrolled on screen from that log. It is not a second live stunt.
- Workers are async actors in one process. The video says so.
- Live fleet size is the largest of 4,000, 2,000, and 500 whose re-solve p50 is under 1,000 ms. Requested megawatts scale as `40 * N / 4000`.
- The quarter-of-time milestone may use `stub_cost` and must print `cost=stub`. After Wear's scalar is green, the demo imports `marginal_wear`.
- No HTTP API. Real-time prices may sit in the cache and are not imported by `allocate.py`.
- The hosted prototype URL is absent from the repo. The page shows the words `synthetic fleet`.
- Reserve is never used to cover a shortfall.
## 4. Interfaces Machine implements
```python
def make_fleet(n: int, seed: int = 7) -> list[UnitState]:
    """Deterministic synthetic fleet. 20 kW, 39.2 kWh.
    Calls Wear.initial_health when that function imports.
    Otherwise health is 1.0 and metadata health_initialized is false.
    """
def allocate(call, units, policy, assumption="nominal") -> tuple[list[Setpoint], float]:
    """policy 'even' and 'most_charge' delegate to Wear.policies.
    policy 'runway' water-fills on a cost scalar.
    Returns (setpoints, shortfall_kw) with shortfall_kw >= 0.
    """
```
Water-fill for `runway`: binary-search a marginal-cost threshold. Each unit's feasible power is `min(p_max_kw, energy_above_reserve_kwh / hours)`. On a 21-point grid of that interval, take the largest power whose cost scalar is at or below the threshold. If the sum is short of the request, `shortfall_kw` is the gap. Feasible power never includes reserve energy.
Until `marginal_wear` imports, the scalar is:
```python
def stub_cost(u, call, power_kw, dt_s, assumption="nominal") -> float:
    return 1.0 + u.sun_exposure + max(0.0, 1.0 - u.health)
```
Any command that uses the stub prints `cost=stub`. `tests/test_no_stub_in_demo.py` fails if the demo still imports the stub after Wear's monotonicity test is green.
Dispatcher:
- One async actor per unit, heartbeats, append-only journal, monotonic `seq` on every setpoint.
- A missed heartbeat marks that unit offline and triggers a new solve.
- The worker also refuses a setpoint that would enter reserve.
- Killing the dispatcher process and starting it again applies each `seq` once and keeps the same energy totals.
- Faults the worker can inject: offline, derated, stale telemetry, under-delivery. The filmed fault is offline.
Calendar: execute `CALLING_RULE.md` byte for byte. Write `handoff/calls.parquet` with `call_id`, `utility` (`Austin Energy`), `start` (UTC), `duration_min` (90), `mw_requested`, `peak_odds`, `ambient_c` (Austin), `source` (`4cp_candidate`), `hit`, `miss_reason`, `rule_hash`, `forecast_peak_mw`. Hash the rule file into `rule_hash`. A later edit to the 0.97 threshold fails CI.
## 5. Outputs
| Path | Contents |
| --- | --- |
| `data/parquet/`, `data/manifest.json` | 2025 ERCOT system load, day-ahead forecast, published 4CP intervals, load-zone prices; hourly weather for Austin, Seguin, Denton |
| `data/fixture/` | June 2025 slice committed on the first successful pull. The quarter milestone reads this slice |
| `handoff/calls.parquet` | Candidate calls for Wear |
| `outputs/m1/dispatch.json` | 32 setpoints for the published June 2025 interval, `synthetic: true` |
| `outputs/track2/metrics.json` | Seed, N, MW, fault, tracking error, shortfall, reserve violations, re-solve latency, seq before and after, cost name |
| `outputs/bench/allocate.json` | p50 milliseconds at 1,000, 10,000, and 100,000 units, CPU model, timer wrapped around `allocate` only |
| `outputs/demo/decision_table.csv` | `unit_id,power_kw,enclosure_c,marginal_wear,assignment` for units whose setpoint changed on the re-solve |
| `web/index.html` | Thermal fleet, water-fill, heat curve, replay |
| `VIDEO_SCRIPT.md` | Five-minute script, each number cited |
`metrics.json` shape:
```json
{
  "seed": 7,
  "n_units": 2000,
  "mw_requested": 20.0,
  "fault": "offline_wave",
  "fraction_offline": 0.15,
  "tracking_error_kw": 0.0,
  "shortfall_kw": 0.0,
  "reserve_violations": 0,
  "resole_latency_ms": 0.0,
  "seq_before": 1,
  "seq_after": 2,
  "cost": "marginal_wear"
}
```
`cost` is `stub` on any run that has not yet imported Wear's scalar. Call progress uses simulated time. `resole_latency_ms` uses the wall clock. The UI labels both.
## 6. Acceptance tests
| Test | Pass condition |
| --- | --- |
| `tests/test_contracts.py` | Signatures match the vision spec. The guard rejects a setpoint that enters reserve. Fleet seed 7 is deterministic and tagged synthetic. |
| `tests/test_ingest.py` | `python -m runway.ingest --offline` matches `manifest.json` with the network disabled. |
| `tests/test_calendar.py` | Every June–September 2025 published 4CP timestamp is a hit or has a `miss_reason`. `rule_hash` matches `CALLING_RULE.md`. The 0.97 value is unchanged after `hit_flags.json` exists. |
| `tests/test_allocate.py` | Box limits, shortfall identity, determinism, reserve guard, including a fleet that cannot meet the request. `even` and `most_charge` match `policies.py` within `1e-6` on 32 units. |
| `tests/test_worker.py` | Offline, derate, stale, and under-delivery each change telemetry the dispatcher reads. The worker refuses an illegal setpoint. |
| `tests/test_dispatcher.py` | Seed-7 wave writes `reserve_violations == 0` and a new seq. Subprocess kill plus restart applies each seq once. |
| `tests/test_bench.py` | Three timings, no workers started, CPU recorded. |
| `tests/test_demo_binding.py` | `python -m runway.demo --seed 7 --fault offline_wave` calls the dispatcher under test. Pre-fault setpoints match the chaos test. |
The dashboard check: the page loads with the network off, the hosted artifact URL appears nowhere in the repo, the DOM contains `synthetic fleet`, and editing the fixture hero number changes the rendered number.
## 7. Hours and order
| Order | Work | Hours | Unblocked by |
| --- | --- | --- | --- |
| M1 | Contracts, fleet, reserve tests | 2 | Joint freeze |
| M2 | Ingest and June fixture | 4 | M1 |
| M3 | Calendar | 4 | M2 and frozen `CALLING_RULE.md` |
| M4 | Water-fill, stub allowed | 4 | M1. Can run beside M2 |
| M5 | Quarter milestone, 32 units, June interval | 3 | M2 and M4 |
| M6 | Workers | 4 | M1 |
| M7 | Dispatcher, journal, restart, ladder | 6 | M4 and M6 |
| M8 | Demo entrypoint bound to seed 7 | 2 | M7 |
| M9 | Benchmark | 2 | M4 |
| M10 | Dashboard, four views | 3 | M1 for the shell; real files as they appear |
| M11 | One command, decision table, fallback recording, script | 4 | M8. Hero chart splices in when Wear's file exists |
| M12 | 12CP count table | 2 | M3. Drop this first |
About 36 focused hours if M12 stays, 34 if it goes. Coding agents can take M2, M6, M9, and the dashboard shell. Machine reviews the dispatcher and the calendar personally.
The quarter milestone (M5) does not wait on Wear's BLAST run.
## 8. Handoff to Wear
`handoff/calls.parquet` is the only ERCOT product Wear needs. Machine publishes it as soon as `rule_hash` matches. Wear's hero swaps off the four-row fixture at that moment.
Machine imports `marginal_wear` the hour Wear says the monotonicity test is green. If Wear says the scalar cannot be made nondecreasing, Machine switches the runway policy to a damped search and still prints the SLSQP gap on 16 or fewer units.
## 9. If Wear is late
M5 ships on `stub_cost` and is labeled. The chaos test stays on the stub. The live fault can still be filmed: it is the Track 2 proof. Physics minutes stay off camera until `hero.json` exists with `policy_for_ratio` equal to `even`. Machine does not invent a temperature or aging model to fill the gap.
## 10. Video minutes Machine fills
- 1:40–2:20, decision table, saturation row, benchmark.
- 2:20–3:40, live fault and the restart log.
- 4:30–5:00, the one command and the assumption labels.
Machine edits the full five minutes. Wear approves every physics line against `PHYSICS_LINES.md`.
Record the fault beat when M8 is green. Record the fallback of that same command before 85% of the clock. Add the hero chart when `hero.json` lands. One recording, seed 7.
## 11. Cut order
1. M12, the 12CP table.
2. Real-time prices in any chart.
3. The dashboard. The CLI, the decision table, and `metrics.json` carry Machine's minutes.
4. The 100,000-unit benchmark row. Keep 1,000 and 10,000.
5. Seguin and Denton anywhere Wear's hero reads them. Austin ambient stays. The extra cities may remain in the cache.
## 12. Done
Machine is done when the tests in section 6 pass with the network off, `python -m runway.demo --seed 7 --fault offline_wave` is the chaos test, the decision table and metrics match the schemas, the dashboard shows only pipeline JSON, and `VIDEO_SCRIPT.md` cites every spoken number.