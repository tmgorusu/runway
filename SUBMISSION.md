# Runway: Devpost submission

Paste-ready text for each Devpost field. Every number cites the file that produced it, so it can be checked against the repo.

## Tagline

Wear-aware dispatch for utility-called home batteries: fill the megawatts where they cost the fleet the least battery life, never touch backup reserve, and report the shortfall when the fleet can't cover the call.

## Tracks

- **Track 1, Open Grid Data:** a pre-registered hunt for ERCOT's 2025 4CP intervals, and what that hunt costs a battery fleet in wear.
- **Track 2, Orchestration:** a dispatcher that survives a seeded wave of offline units and a SIGKILL mid-call without drawing reserve or double-applying a setpoint.

## Inspiration

A home battery on a utility program spends its life twice. Once for the homeowner's backup, once for the grid. The grid half is spent on calls, and most calls are guesses. ERCOT's transmission charges are set by four 15-minute intervals a year (4CP), so fleets call often, hoping one lands in the window.

We asked two questions. How much battery life does that hunting cost? And if the calls are going to happen anyway, can the fleet choose *which* homes answer so the same energy wears it less? A battery bolted to a west-facing wall in Austin runs several degrees hotter than one in a shaded carport. For the same kWh, it loses more capacity.

## What it does

- **Hunts the 2025 peaks honestly.** A calling rule is committed before any hit-rate file exists: a day is a candidate when its day-ahead ERCOT peak forecast reaches 0.97 of the month's running maximum. Its sha256 is stamped on every call. On real 2025 data it flags 52 candidate days and catches all 4 published 4CP intervals (`handoff/hit_flags.json`).
- **Prices the hunt in battery life.** Under an even split across a 4,000-unit synthetic fleet, 91.9% of summer call wear lands on calls that missed every 4CP interval. A missed-call kWh costs 0.946× a hit kWh, so a miss is not cheaper (`outputs/track1/hero.json`).
- **Routes each call by marginal wear.** For every unit and power level, Runway estimates the extra capacity loss from delivering that power. The thermal model feeds the aging model, and the derivative is taken numerically. A water-fill then assigns the megawatts where that cost is lowest. Two units delivering the same 15 kWh differ by 1.27× in capacity lost (`outputs/physics/reference_pair.json`). Over the hero calls, Runway uses 3.4% less total capacity than even spread and 29% less than most-charge-first. In the summer replay it uses the least in every assumption set (`outputs/replay/detail.json`).
- **Keeps running when units fail.** 4,000 async unit workers heartbeat to a dispatcher with a write-ahead journal. A seeded fault takes the 15% of units holding the largest setpoints offline. The dispatcher detects the missed heartbeats and re-solves in 89 ms wall-clock, still delivering 40.000 MW with 0 shortfall and 0 reserve violations (`outputs/track2/metrics.json`). Killing the dispatcher process and restarting it applies each setpoint batch exactly once (`outputs/track2/restart.log`).
- **Targets the right subgrid when Austin's grid misbehaves.** An interactive map of the Austin Energy territory shows 58 anomaly days in 2025: 4CP candidates, LZ_AEN price spikes, and heat anomalies. For each one, a three-layer game picks which of 30 synthetic feeder zones to call. The utility posts a price, zones respond with the power whose marginal wear is at or below it (within hosting limits), a local search trims zones whose activation cost outweighs their help, and Shapley values split the credit. Users can knock out feeders and re-solve live in the browser. On the July 30 4CP call it targets 22 zones with zero feeder overloads, where even spread overloads 7, for 5.5% more true wear (`outputs/austin/events.json`).
- **Is honest about limits.** A fleet too small for the call (2,000 units asked for 40 MW) falls 5.8 MW short under every policy, because leveling cannot create megawatts. Called every day for ten years, every policy loses the same 236 units (`outputs/replay/summary.json`).

## How we built it

We split the build into two halves with a frozen contract, then ran each half past its own review council before building.

- **Machine** built the offline data cache, the calendar that executes the calling rule, the allocator, the async workers, the journaled dispatcher, the benchmark, the dashboard, and the one-command demo. Data sources: EIA-930 hourly ERCOT demand and day-ahead forecast, ERCOT's published 4CP report, Open-Meteo weather.
- **Wear** built the enclosure thermal model, the LFP aging model, the marginal-wear scalar, the baseline policies, the hero ratio, and the season replay.
  - **Aging:** the calendar and cycle fade follow NREL BLAST-Lite's large-format LFP model, re-referenced at 25 °C. It matches a committed BLAST-Lite run within 0.2% on calendar fade and 1.4% on cycle fade, with zero fitted parameters (`outputs/physics/blast_overlay.json`).
  - **Assumption sets:** three sets bracket every uncertain number, each labeled sourced or assumption.
- **Synthetic telemetry.** There is no Base telemetry, so we generated a synthetic stand-in for what a fleet operator would plausibly report. It covers install dates, mounting, daily enclosure temperature, SOC, throughput, BMS state of health, and heartbeats with outages, driven by real Austin weather and the real 2025 call days. Runway fits each unit's state from April–May data only, before the summer replay starts. The fitted thermal exposure tracks the hidden truth with correlation 0.996 (`outputs/physics/telemetry_fit.json`).
- **Stack:** Python 3.12, numpy, pandas, pyarrow, asyncio, scipy (only for an SLSQP optimality check), and uv. The dashboard is a single self-contained HTML file. 115 tests, all run with the network blocked.

## Challenges we ran into

- **The reference model disagreed with the physics.** BLAST-Lite's large-format LFP model makes cycle fade *decrease* with temperature, and its own documentation flags that as a fitting artifact. We kept BLAST-Lite's magnitudes, used a published Arrhenius term for temperature, and put the disagreement on the record: at 45 °C our cycle fade is 3.6× BLAST-Lite's. With BLAST-Lite's small-cell LFP data, where temperature doesn't affect cycling, the hot/cool routing effect disappears. The pitch rests on that one parameter, and we say so.
- **Our first tuning made things worse.** A "glide path" weight that favored units behind their contract schedule made Runway use 6.9% *more* capacity than even spread. A straight-line path makes older, slower-aging units look ahead of schedule. We turned it off and kept it as a reported sensitivity run (`outputs/physics/glide_sensitivity.json`).
- **Pre-registration cost us a clean headline.** The hit rate and the wear ratio are what they are. We committed the calling rule before looking, and a ratio near 1.0 ships as a result, not a bug.
- **Monotonicity.** The allocator's water-fill needs cost to never decrease as power rises. We constrained the model so wear is convex in power. The derivative is then provably nondecreasing, and a test checks it on 401-point grids across every assumption set.

## Accomplishments that we're proud of

- One command, network off, rebuilds every artifact and is itself the chaos test.
- Every number in the video script and dashboard is generated from a file and cites it (`PHYSICS_LINES.md`, `VIDEO_SCRIPT.md`).
- The aging model matches an independent reference with nothing tuned.
- A dispatcher that is killed mid-call recovers from its write-ahead journal without double-applying a setpoint.

## What we learned

- Most of the value in peak hunting is decided by which calls you make. Where the energy comes from is a smaller lever: 1.4% less total capacity lost over the summer replay, and 3.4% less over the call windows, on these assumptions.
- How hot a battery actually runs is not the same as how much sun it gets. Garage heat and enclosure differences matter as much, and telemetry reveals them where an installer survey can't.
- Pre-registering the rule and labeling every assumption made it easier to trust our own results, including the unflattering ones.

## What's next for Runway

- Run it on real fleet telemetry in place of the synthetic stand-in.
- Replace the system-load proxy with each utility's own load in the 4CP intervals.
- Measure the cycle-aging temperature dependence on the actual cells, since the whole effect depends on it.
- Add the 12CP counterfactual for ERCOT's proposed rule change.

## Built with

Python · numpy · pandas · pyarrow · asyncio · scipy · uv · pytest · EIA-930 · ERCOT NP9-83-M · Open-Meteo · NREL BLAST-Lite (reference run only)

## Try it out

- Repository: https://github.com/tmgorusu/runway
- `uv sync && uv run python -m runway.demo --seed 7 --fault offline_wave`, then open `web/index.html`

Synthetic fleet: the batteries and their telemetry are synthetic. ERCOT 2025 dates, load, forecasts, and 4CP intervals are real public data. Nothing here is Base Power data or a claim about how Base Power dispatches.
