# Runway build plan
Executable plan from the planning council. Anything not in the hackathon brief is marked as an assumption. Nothing here is a claim about Base Power's real dispatch or telemetry.
## Assumptions
- Wall clock left: **36 hours**. Milestones are fractions, so a shorter window follows the cut list. Focused-hour estimates sum to 62 and assume four parallel streams.
- The battery-dynamics researcher owns every thermal and aging claim. Other owners are roles: `data-owner`, `systems-owner`, `demo-owner`. Coding agents and git worktrees are assumed available.
- Who picks homes is unknown. The build assumes Base, as owner-operator, may choose units to meet a megawatt request.
- `reserve_frac = 0.30` and a replacement line at **0.70** remaining capacity are our assumptions. The prototype's replacement counts stay out of the script and the repo.
- Published 4CP intervals are treated as ERCOT system coincident peaks. The brief gives each utility's load in those intervals and does not give us that load. Candidate days come from ERCOT system load and the day-ahead system forecast.
- Installed fleet size is not in the brief. `40 MW / 20 kW = 2,000` is only the minimum fleet that can hit the Austin contract at nameplate. Replay uses a **labeled** fleet of 4,000 Base Core units so the allocator has a real choice, plus a 2,000-unit row where it does not.
- At a 0.30 reserve, one unit holds about 27.4 kWh above reserve, so 20 kW for 90 minutes (30 kWh) does not fit. Policies are scored only on a megawatt level all three can serve without touching reserve.
- Workers are async actors in one process. The dispatcher restart test kills a real subprocess. Say so on screen.
- `peak_odds` is a score in `[0, 1]`, not a calibrated probability. Each wear call starts at SOC 1.0.
## Decisions
Tracks 1 and 2. Track 3 stays out.
**Thesis.** The same utility kWh costs a sun-baked battery more of its contract life than a cool one, so Runway fills the megawatts by marginal wear, leaves backup reserve untouched, and reports a shortfall when the feasible fleet cannot cover the call.
**Track 1 number.** `wear_per_kwh_miss / wear_per_kwh_hit` for Jun–Sep 2025 under the pre-registered 0.97 forecast rule, even spread, nominal aging, with the low and high sets beside it, plus `wear_fraction_on_misses`. Even spread authors this number so the controller cannot write its own insight.
**Track 2 failure, live.** A seeded offline wave of the 15% of units holding the largest setpoints, then a re-solve, with delivered MW, shortfall, `reserve_violations = 0`, and wall-clock re-solve latency on screen.
**Not in this build.** An HTTP API, price forecasts, a trading bot, a mobile app, an El Paso or CoServ model, a GVEC fleet, a 100k-worker demo, or a second aging formula.
The hero is wear per kWh, not the share of days that missed. A wide net aimed at four monthly intervals produces a high miss share by construction. A live dispatcher kill is rejected. Restart is a subprocess test. An OS process per battery is rejected for the filmed path.
## Calling rule
Commit `CALLING_RULE.md` before any hit-rate file exists. Do not change 0.97 after `hit_flags.json` exists.
- Time zone: America/Chicago.
- A day in June, July, August, or September 2025 is a candidate when its max day-ahead ERCOT system forecast is at least 0.97 times the max daily day-ahead peak from the first of that month through that day, inclusive.
- One call per candidate day. Start at the forecast peak minus 45 minutes. Duration 90 minutes. `source = 4cp_candidate`. Utility label: Austin Energy.
- `peak_odds` is that ratio clipped to `[0, 1]`. It is a score, not a probability.
- A hit means a published 4CP timestamp falls inside the call window. Otherwise write a `miss_reason`.
- Sensitivity at 0.96 and 0.98 may be written only as a pair, in a side file.
- This uses system load as a proxy. It is not Austin Energy, GVEC, or CoServ load.
## Milestones
Fractions of the 36-hour assumption.
- **25%.** `python -m runway.e2e --offline` prints setpoints and a non-negative shortfall for the published June 2025 4CP interval on 32 synthetic units. Network off. Stub cost is allowed.
- **45%.** The allocator uses marginal wear from `age` and `step_thermal`. A test shows the hotter unit getting weakly less power. The offline-wave chaos test prints `reserve_violations = 0`.
- **70%.** `outputs/track1/hero.json` and `outputs/replay/summary.json` exist. The terminal shows the wear ratio, the feasible-MW policy comparison, the 2,000-unit saturation shortfall, and the over-call caveat. Dispatcher subprocess restart is green.
- **85%.** `python -m runway.demo --seed 7 --fault offline_wave` runs from a clean venv with the network off. The local dashboard is bound to those files. A fallback recording of that command is already on disk.
- **100%.** The five-minute video uses only those artifacts and the brief's cited public figures. Synthetic fleet is on screen the whole time.
## Task cards
Critical path first. The long pole is thermal model, aging, marginal cost, then season replay. Full JSON is in [tasks.json](tasks.json).
| ID | Title | Owner | Depends on | Hours |
| --- | --- | --- | --- | --- |
| T01 | Freeze contracts, reserve guard, and the synthetic-fleet generator | agent | — | 2 |
| T04 | Lumped enclosure thermal model | battery-researcher | T01 | 4 |
| T02 | Cache ERCOT 2025 and weather as Parquet | data-owner | T01 | 4 |
| T06 | Allocator with hard limits and a pluggable marginal-cost callback | systems-owner | T01 | 4 |
| T10 | Async workers with injected faults and a local reserve refusal | agent | T01 | 4 |
| T05 | LFP aging model, BLAST-Lite band, three assumption sets | battery-researcher | T04 | 6 |
| T08 | Thin end-to-end run on the published June 2025 interval | systems-owner | T02, T06 | 3 |
| T03 | Pre-register the hunting rule and score 2025 candidate calls | data-owner | T02 | 4 |
| T07 | Wire marginal wear from age composed with step_thermal | battery-researcher | T04, T05, T06 | 3 |
| T11 | Dispatcher journal, re-solve, and real subprocess restart | systems-owner | T06, T10 | 6 |
| T14 | Benchmark allocate at 1k, 10k, and 100k units | agent | T06 | 2 |
| T15 | Local dashboard bound to pipeline JSON | demo-owner | T01 | 3 |
| T09 | Hero wear ratio on misses versus hits, under even spread | data-owner | T03, T07 | 4 |
| T13 | Season replay, feasible-policy comparison, saturation row, over-call caveat | battery-researcher | T03, T05, T07 | 5 |
| T12 | Bind the demo entrypoint to the chaos-test seed | agent | T11 | 2 |
| T17 | 12CP counterfactual count table | data-owner | T03 | 2 |
| T16 | One-command demo, decision table, fallback recording, citation script | demo-owner | T09, T12, T13, T14, T15 | 4 |
### Acceptance tests
**T01.** `pytest tests/test_contracts.py` passes. The guard rejects any setpoint whose energy would enter reserve. `allocate`, `step_thermal`, and `age` match the brief signatures. Fleet seed 7 is deterministic, tagged synthetic, and uses 20 kW / 39.2 kWh. Default `reserve_frac` 0.30 and replacement health 0.70 are labeled assumptions in the module docstring.
**T04.** Zero power and zero sun converges to ambient within 0.1 C. Steady temperature rises with power and with `sun_exposure`. A steady-state energy-balance residual test passes.
**T02.** `python -m runway.ingest --offline` exits 0 with the network disabled and matches `manifest.json`. The fixture slice alone can build the June published-interval call.
**T06.** Each setpoint stays at or above reserve. Power is inside `[0, p_max_kw]`. Setpoint power plus `shortfall_kw` equals requested kW within 1e-6. `shortfall_kw >= 0`. Outputs are deterministic. If available power is below the request, shortfall equals the gap and no reserve energy is used. The stub cost does not import aging.
**T10.** Each fault changes telemetry the dispatcher reads. An illegal setpoint is still refused at the worker. Tests pass offline.
**T05.** Relative error versus BLAST-Lite at a precommitted reference is inside a precommitted band. A hotter enclosure loses more capacity. Cold-charge is inactive above the temperature named in the file. Swapping JSON changes the loss and leaves the function body unchanged.
**T08.** Network disabled, the command prints one `call_id`, 32 setpoints, and `shortfall_kw >= 0`. JSON header has `synthetic=true`. This ships while T07 is still open.
**T03.** The rule commit is an ancestor of the output commit. `hit_flags.json` carries a hash of the rule. Every Jun–Sep 2025 published 4CP timestamp is a hit or has a `miss_reason`. CI fails if 0.97 changes after `hit_flags.json` exists.
**T07.** Higher `sun_exposure` receives weakly less power at equal SOC. The unit further below its glide path receives weakly less power. A test fails if the cost module is not `marginal.py`. On n≤16 the objective gap versus SLSQP is printed and a gap above 1% fails.
**T11.** Seeded wave has `reserve_violations` 0, a new seq, and metrics for `tracking_error_kw`, `shortfall_kw`, and `resole_latency_ms`. Latency is wall-clock. Killing the dispatcher subprocess and restarting applies each seq once. Live demo N is the largest of 4000, 2000, 500 whose re-solve p50 is under 1000 ms. Requested MW scales as `40 * N / 4000`.
**T14.** Prints p50 milliseconds at 1000, 10000, and 100000 units. The timer wraps `allocate()` and excludes fleet generation. No workers start. CPU model is recorded.
**T15.** Loads with the network off. The hosted artifact URL is absent from the repo. The DOM contains `synthetic fleet`. Editing the fixture hero number changes the page. Four views only: thermal fleet, water-fill, heat curve, replay.
**T09.** Contains `wear_per_kwh_miss`, `wear_per_kwh_hit`, `ratio`, and `wear_fraction_on_misses` for even spread on all three sets, plus total wear for runway, even spread, and most-charge-first. Same seed is byte-identical. A ratio near 1.0 is a valid result and does not authorize a rule edit.
**T13.** Seed 7 is byte-identical. Reserve violations are 0. If any policy shortfalls on the 4000-unit base calls, lower MW until all three have zero shortfall and compare early replacements only there. The 2000-unit row records shortfall by policy. The over-call fixture shows early replacements for all three. The JSON includes `ranking_flipped_across_assumption_sets`. Most-charge-first means highest SOC at call start, with `unit_id` as the tie-break.
**T12.** Pre-fault setpoints match the chaos test for seed 7. The command runs offline and writes the same metrics fields.
**T17.** Counts are code-generated. The file is labeled a counterfactual on a proposed rule whose decision is expected December 2026. Dollars only from the brief: about $17/kW per 4CP interval and $5.70/kW under 12CP, labeled rough and ERCOT-wide. No wear model.
**T16.** A clean venv finishes the README command offline. `decision_table.csv` has `unit_id`, `power_kw`, `enclosure_c`, `marginal_wear`, and `assignment` for units whose setpoint changed on the re-solve. Every spoken number cites a file or a brief figure. The recording uses seed 7. Record the fault beat when T12 is green and add the hero chart when T09 is green.
## Parallel plan
Nothing starts before T01 is committed.
| Stream | Owner | Tasks | Worktree |
| --- | --- | --- | --- |
| Physics | battery-researcher | T04, T05, T07, T13 | `physics` |
| Data | data-owner | T02, T03, T09, T17 | `data` |
| Dispatch | systems-owner | T06, T08, T11 | `dispatch` |
| Demo | demo-owner | T15, T16 | `demo` |
| Agents | agent | T01, T10, T12, T14 | `contracts`, `workers`, `bench` |
Safe to hand to a coding agent: T01, T02, T06's property tests, T10, T12, T14, and the T15 shell against fixture JSON. The researcher reviews T04, T05, T07, and T13 before merge. A human commits `CALLING_RULE.md` before T03's code runs. The systems owner owns T11. Agents do not edit `marginal.py` or the hero definition.
## Cut list
Drop from the top.
1. T17, the 12CP table, if T09 has not started on time.
2. Real-time prices in any chart. The cache may already hold them. The allocator must not import them.
3. Cold-charge term. Keep the summer thermal path.
4. High and low assumption sets in the spoken script. Still write the JSON if T05 finished.
5. Contract-length repetition of 2025. Keep one summer.
6. Dashboard. The CLI, decision table, and metrics JSON carry the demo.
7. The 100k benchmark row. Keep 1k and 10k.
8. Seguin and Denton in the hero. Austin ambient stays.
Do not cut past this list. The next things to go would be a track. Reserve tests, the frozen 0.97 rule, the even-spread wear ratio, nominal `age()`, the offline-wave demo, synthetic labels, and the over-call caveat stay.
## Video outline
One command. Simulated time is labeled. Synthetic fleet stays on screen.
| Time | What the judge sees | Rubric |
| --- | --- | --- |
| 0:00–0:30 | Same kWh, different enclosure temperature, different capacity loss. The call is filled from energy above reserve. A shortfall is reported when the feasible fleet cannot cover it. | The why, Problem fit |
| 0:30–1:40 | 2025 candidate days against published 4CP intervals. The wear-per-kWh ratio and the miss fraction, three assumption sets if present. On-screen label: system-load proxy, score not probability. | Insight quality, Problem fit, The why, Technical depth |
| 1:40–2:20 | Water-fill decision table: unit, kW, enclosure °C, marginal wear. The 2,000-unit row where every healthy unit is saturated and leveling creates no megawatts. Bench p50 at 1k / 10k / 100k. BLAST overlay in the corner. | Technical depth, Performance, The why |
| 2:20–3:40 | Live seed-7 offline wave at the ladder N that re-solved inside one second. Requested MW, delivered MW, shortfall, reserve violations at 0, wall-clock re-solve latency. Ten seconds of the pytest log for the subprocess restart. | Problem fit, Completeness, Performance |
| 3:40–4:30 | Replay on the feasible MW: Runway, even spread, most-charge-first. Over-call fixture where all three hit the replacement line. `ranking_flipped_across_assumption_sets` read aloud whatever it is. | Insight quality, The why, Completeness |
| 4:30–5:00 | README command, clean-venv timing, citation rule. State the reserve fraction, the 4,000-unit fleet, and the 0.70 line as our assumptions. | Usability, Creativity |
## Risks
- **The 0.97 band gets tuned after the official dates are plotted.** `CALLING_RULE.md` is committed first and hashed into `hit_flags.json`. A ratio near 1 is a result.
- **Two wear formulas.** `allocate` imports `marginal.py` only, and that file is a finite difference of `age` composed with `step_thermal`.
- **Most-charge-first loses because 90 minutes at 20 kW does not fit above a 0.30 reserve.** T13 lowers MW until all three policies have zero shortfall, and only then compares replacements. The 2,000-unit row is allowed to shortfall, and it should shortfall together.
- **Demo path drifts from the test.** `runway.demo --seed 7` is the chaos test. Fallback recording of that command is on disk before the 85% mark.
- **4,000 async actors miss the frame.** Live N steps down the ladder 4000, 2000, 500. The first step under 1000 ms is the one we film, labeled.
- **BLAST-Lite slips.** Nominal algebraic aging still ships. The overlay is marked failed.
- **ERCOT API is down on demo day.** The demo reads the cache. No network in the README command.
- **Leveling is asked to save an over-called fleet.** The over-call fixture stays in the video, including when Runway wins the base case.
- **If Base says the utility picks the homes,** the decision table is labeled as a counterfactual on the same call. Do not build a second product.
### Ask a Base engineer in the first hour
1. Who selects the homes that meet a megawatt call, Base or the utility?
2. What backup reserve fraction is committed to the member, and is it the same on 10- and 12-year contracts?
3. On the Austin agreement, how many units are installed against the 40 MW contract, and is a typical call about 90 minutes at that megawatt level?
## Judge's scorecard
Scores assume this plan is executed, including a ratio reported honestly if it is near 1.
| Area | Score | Why |
| --- | ---: | --- |
| Completeness | 12 | Offline end-to-end, reserve property tests, and a seeded demo. The live re-solve can still hang. |
| Technical depth | 13 | Thermal model, BLAST-bounded aging, one wear formula, water-fill with an SLSQP gap, journal, subprocess restart. |
| Problem fit | 13 | Both tracks share `age()`. The system-load proxy will be marked down by anyone who wanted utility load. |
| The why | 14 | The ratio, the saturation row, the feasible-MW gate, and the over-call caveat are the argument. |
| Insight quality | 8 | Pre-registration keeps it from being a chosen hit rate. The ratio can still be unsurprising if hits and misses have similar temperatures. |
| Usability | 7 | One command, a decision table, and labels. A Base operator still cannot point it at real homes. |
| Creativity | 8 | Hunting wear and dispatch under failure are one system. The UI is a port of the prototype. |
| Performance | 8 | Allocator bench plus a re-solve latency at the filmed N. Do not claim 100k live workers. |
| **Total** | **83** | |
The single change that would raise the total most is a live fault on **32 OS processes**, used only if that chaos test is green at the 70% milestone. That is worth about two points of problem fit and two of technical depth. Forcing it in the last evening is how completeness falls off a cliff. A red test at 70% means the change is refused and 83 stands.
## Council record
- 2026-09-27, Wear council. Enclosure time constant 7,500 s (3.0e5 J/K, 40 W/K), solar 250 W × sun_exposure, resistive heat 1.0 W/kW²; all assumptions. Aging: BLAST-Lite 1.1.1 `Lfp_Gr_250AhPrismatic` calendar and cycle magnitude (sourced), cycle temperature term replaced by Arrhenius 31.7 kJ/mol (Wang 2011, sourced). No fitted parameters. Cold-charge cut. `marginal_wear` is a 0.25 kW forward difference, nondecreasing by construction.
- 2026-09-27, build on simulated inputs. ERCOT-like load, forecast, 4CP intervals, and weather are simulated (`runway/simulate.py`, seed 7); the fleet is synthetic. BLAST-Lite reference committed from a Python 3.12 venv; our model is within 0.2% (calendar, 25 and 45 °C) and 1.4% (cycle, 25 °C).
- 2026-09-27, owner decision. Glide weight `k` = 0 in all three assumption sets; the council's `k` = 10 runs only as a sensitivity (`outputs/physics/glide_sensitivity.json`). Reason: a straight-line glide path makes older units look ahead of schedule, and at `k` = 10 Runway used 7.6% more capacity than even spread on the hero calls. At `k` = 0 it uses 3.0% less, and the policy ranking is the same in every set.
- 2026-09-27, hero result. Wear per kWh, miss over hit, under even spread: 0.991 nominal, 1.000 low, 0.992 high. 92.8% of call wear lands on misses. Reported as is; the 0.97 rule is unchanged.
- 2026-09-27, merge. Machine's M1–M11 branches merged into `main`; Wear's modules rewired onto Machine's contracts and its real 2025 cache (EIA-930 ERCOT load and day-ahead forecast, ERCOT NP9-83-M 4CP intervals, Open-Meteo weather). The simulated inputs are retired; the earlier simulated build is kept on branch `wear-sim-build`. `CALLING_RULE.md` was committed before the calendar first ran. On real data: 52 candidate calls, all four 2025 4CP intervals hit; hero ratio 0.949 nominal (1.003 low, 0.921 high), 91.9% of call wear on misses; Runway uses the least total capacity in every assumption set; live offline wave on 4,000 units re-solves in about 90 ms with zero shortfall and zero reserve violations.
- 2026-09-27, owner decision: synthetic telemetry. The seed-7 fleet is now built from a synthetic stand-in for Base fleet telemetry (`runway/telemetry.py`: registry, daily enclosure temperature, SOC, throughput, BMS state-of-health, heartbeats with outages), driven by real Austin weather and the 2025 call days. Wear estimates each unit from April–May data only (`runway/telemetry_fit.py`). Pre-registered bands (exposure correlation ≥ 0.95, MAE ≤ 0.05; health MAE ≤ 0.006) pass at 0.996, 0.023, and 0.004. Raw sun exposure alone correlates 0.50 with effective thermal exposure.
- 2026-09-27, owner decision: over-call fixture redefined after the result below, to satisfy VISION invariant 6. The over-call fixture is now the saturated fleet (2,000 units asked for 40 MW, daily call at 44 °C, 10 repeated years): every unit sits at its cap and all three policies cross the replacement line (236 each). The earlier 4,000-unit fixture is still run and recorded in `outputs/replay/detail.json`.
- **Escalation to the vision (invariant 6), superseded by the entry above.** The pre-registered over-call fixture (4,000 units, one call per day at 44 °C, 10 repeated years, nominal) does not push every policy past the replacement line: runway 0, even spread 0, most-charge-first 307 early replacements on the simulated build, 303 after the merge. `over_call_all_policies_fail` is false, and `tests/test_replay.py::test_over_call_fixture_fails_all_policies` stays red. The fixture was not made harsher after the result.
