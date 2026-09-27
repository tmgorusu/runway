# Wear specification
Owner: the battery-dynamics researcher.
Wear produces every number about temperature, aging, and the ERCOT wear ratio. Machine produces the cache, the water-fill shell, the workers, the dispatcher, and the demo. The merged picture is `specs/VISION.md`. This file is the half Wear can build and test alone.
## 1. Responsibility
Wear implements and owns:
- `CALLING_RULE.md` (the text; Machine executes it)
- `runway/thermal.py`
- `runway/aging.py`
- `runway/marginal.py`
- `runway/policies.py` (`even` and `most_charge` only)
- `runway/hero.py`
- `runway/replay.py`
- `assumptions/nominal.json`, `assumptions/low_sensitivity.json`, `assumptions/high_sensitivity.json`
- `PHYSICS_LINES.md`
- tests named in section 6
Wear does not edit `allocate.py`, `dispatcher.py`, `worker.py`, `ingest.py`, `calendar.py`, or `web/`.
## 2. Decisions Wear may make
- Enclosure time-constant, solar gain, and resistive-heating coefficients.
- How the algebraic aging model is fit to an in-repo BLAST-Lite run, and the width of the precommitted error band.
- The numeric spreads inside the three assumption files, including glide-path `k`.
- The integration substep inside a 90-minute call.
- Whether the cold-charge term ships. It is first on Wear's cut list.
## 3. Decisions already closed
Wear's council inherits these. They are specified in `specs/VISION.md` and are not reopened here.
- Hero ratio is wear per kWh on misses divided by wear per kWh on hits, authored by **even spread**, for June–September 2025, nominal set, with low and high beside it, plus the fraction of wear on misses.
- A ratio near 1.0 is a valid result. `CALLING_RULE.md` stays at 0.97.
- Reserve default 0.30 and replacement at health 0.70 are labeled assumptions.
- Replay fleet is 4,000 synthetic Base Core units at 40 MW. Saturation row is 2,000 units at 40 MW.
- Each hero call starts at SOC 1.0, and `hero.json` says so.
- Early replacements are compared only at a megawatt level all three policies can serve with zero shortfall.
- The over-call fixture fails all three policies.
- Prototype replacement counts stay out of the repo and the script.
## 4. Interfaces Wear implements
```python
def step_thermal(u: UnitState, ambient_c: float, power_kw: float, dt_s: float) -> float:
    """Enclosure temperature in °C after dt_s.
    Heat terms: exchange with ambient, solar gain scaled by sun_exposure, resistive heating.
    """
def age(u, enclosure_c, power_kw, dt_s, assumption="nominal") -> float:
    """Capacity fraction lost during dt_s. Positive degrades the cell.
    Terms: Arrhenius calendar, Arrhenius cycle, C-rate, and a cold-charge penalty
    that is inactive above the temperature named in the assumption file.
    """
def initial_health(age_years: float, term_years: int, assumption="nominal") -> float:
    """Remaining capacity fraction at the start of a replay."""
def marginal_wear(u, call, power_kw, dt_s, assumption="nominal") -> float:
    """One positive scalar. Higher means more costly to dispatch this unit at this power.
    Finite difference of age(step_thermal(...)).
    Multiplied by the glide weight and, when telemetry is stale on a high peak score, by 2.
    Nondecreasing in power_kw at fixed unit and call.
    """
```
Glide weight, inside `marginal_wear`:
```text
target = 1.0 - (1.0 - 0.70) * (age_years / term_years)
gap    = target - health          # positive: behind the path
weight = exp(k * gap)             # k lives in the assumption file
```
Stale telemetry: if `call.start - last_seen` exceeds 15 minutes and `peak_odds >= 0.9`, multiply the scalar by 2.
`policies.py` holds the two baselines. Both clip every unit to `min(p_max_kw, energy_above_reserve_kwh / hours)`.
- **even.** Equal kilowatts, then residual headroom filled in `unit_id` order until the request is met or every unit is capped.
- **most_charge.** Sort by SOC descending, `unit_id` ascending. Fill each unit to the cap until the request is met.
Machine's `allocate` calls these functions for those two policies and calls `marginal_wear` for `runway`. Wear does not ship a second water-fill.
## 5. Outputs
### `outputs/track1/hero.json`
Written by Wear. The ratio block uses `policy="even"` on Machine's `handoff/calls.parquet` once `rule_hash` matches `CALLING_RULE.md`. Until that parquet exists, Wear runs the same code on a four-row fixture with the same columns.
```json
{
  "synthetic": true,
  "fleet_n": 4000,
  "seed": 7,
  "policy_for_ratio": "even",
  "rule_hash": "",
  "soc_each_call": 1.0,
  "assumption_sets": {
    "nominal": {
      "wear_per_kwh_miss": 0.0,
      "wear_per_kwh_hit": 0.0,
      "ratio": 0.0,
      "wear_fraction_on_misses": 0.0,
      "kwh_miss": 0.0,
      "kwh_hit": 0.0
    }
  },
  "total_capacity_fraction_lost": {
    "runway": 0.0,
    "even": 0.0,
    "most_charge": 0.0
  }
}
```
`low_sensitivity` and `high_sensitivity` repeat the ratio block.
### `outputs/replay/summary.json`
```json
{
  "synthetic": true,
  "seed": 7,
  "feasible_mw": 40.0,
  "replacement_health": 0.70,
  "reserve_frac": 0.30,
  "reserve_violations": {"runway": 0, "even": 0, "most_charge": 0},
  "early_replacements": {"runway": 0, "even": 0, "most_charge": 0},
  "saturated_2000_shortfall_kw": {"runway": 0.0, "even": 0.0, "most_charge": 0.0},
  "over_call_all_policies_fail": true,
  "ranking_flipped_across_assumption_sets": false,
  "repeated_2025_labeled": true
}
```
`ranking_flipped_across_assumption_sets` is the boolean the runs produced.
Replay loop, per call: request setpoints from `allocate`, reject any reserve breach, integrate `step_thermal` and `age` at Wear's chosen substep, and apply calendar aging at ambient to units with zero power. Units whose health falls below 0.70 count as early replacements.
If any policy shortfalls at 40 MW on the 4,000-unit fleet, Wear lowers the megawatts until all three have zero shortfall, stores that level in `feasible_mw`, and counts replacements only there.
Repeating the 2025 weather and call pattern out to a contract term is allowed only with `repeated_2025_labeled: true`.
### `outputs/physics/blast_overlay.json`
Either a real overlay against the in-repo BLAST-Lite run, or `{"status": "failed"}` if that run is unavailable. The video shows the overlay only when the status is a success.
### `PHYSICS_LINES.md`
Every physics sentence for the video, each with the file that produced the number. Machine does not add a physics number that is missing from this sheet.
## 6. Acceptance tests
| Test | Pass condition |
| --- | --- |
| `tests/test_thermal.py` | Zero power and zero sun settles within 0.1 °C of ambient. Steady temperature rises with power and with `sun_exposure`. Energy-balance residual is inside a tolerance written in the test beforehand. |
| `tests/test_aging.py` | At a reference committed before the comparison, relative error versus BLAST-Lite is inside a band committed beforehand. A hotter enclosure loses more capacity. Swapping an assumption file changes the loss and leaves the function body unchanged. Cold-charge is inactive above its named temperature. |
| `tests/test_marginal.py` | The scalar is a finite difference of `age` composed with `step_thermal`. It does not decrease as power increases. Higher sun exposure costs more at equal SOC. The unit further below its glide path costs more. On 16 or fewer units the SLSQP gap is printed and a gap above 1% fails. |
| `tests/test_policies.py` | Even and most-charge respect power caps and reserve, match across processes, and report shortfall when the fleet cannot cover the request. Most-charge order is SOC descending, `unit_id` ascending. |
| `tests/test_hero.py` | Schema matches section 5. `policy_for_ratio` is `even`. Same seed is byte-identical. |
| `tests/test_replay.py` | Reserve violations are 0. Replacements are counted only at `feasible_mw`. The 2,000-unit row records shortfall for each policy. The over-call fixture sets `over_call_all_policies_fail` true. |
## 7. Hours and order
| Order | Work | Hours | Unblocked by |
| --- | --- | --- | --- |
| W1 | `policies.py` | 2 | Joint freeze |
| W2 | `step_thermal` | 4 | Freeze |
| W3 | `age`, assumption files, BLAST overlay | 6 | W2 |
| W4 | `initial_health`, `marginal_wear` | 3 | W3 |
| W5 | Hero | 4 | W4, plus a four-row fixture; real parquet swaps in later |
| W6 | Replay | 5 | W4 and W1. `runway` totals wait on Machine's `allocate` |
| W7 | `PHYSICS_LINES.md` | 2 | W5 and W6 |
About 26 focused hours. W3 is the long pole. If BLAST-Lite is not running by hour 12, ship the nominal algebraic model, mark the overlay failed, and continue.
## 8. Handoff from Machine
Wear reads `handoff/calls.parquet` and does not re-download ERCOT. Required columns: `call_id`, `utility`, `start`, `duration_min`, `mw_requested`, `peak_odds`, `ambient_c`, `source`, `hit`, `miss_reason`, `rule_hash`, `forecast_peak_mw`. Austin ambient is the temperature input. Seguin and Denton stay out of the hero.
Wear tells Machine in the same hour if `marginal_wear` cannot be made nondecreasing in power. Machine's grid search depends on that property.
## 9. If Machine is late
Hero and the even / most-charge replay run through `policies.py` and the four-row fixture. `total_capacity_fraction_lost.runway` is `"blocked_on_allocate"` until Machine's water-fill imports `marginal_wear`. Wear does not write a second allocator and does not fetch ERCOT.
## 10. Video minutes Wear fills
- 0:00–0:30, why the same kWh is a different cost.
- 0:30–1:40, the 2025 ratio.
- 3:40–4:30, the replay, the saturation row's meaning, and the over-call caveat.
Wear reads every physics number before it is spoken.
## 11. Cut order
1. Cold-charge term.
2. High and low sets in the spoken script. They still land in `hero.json` if W3 finished.
3. Repeating 2025 out to a 10- or 12-year term. One summer remains.
4. Sun-exposure sweep beyond a single reversal note in `summary.json`.
## 12. Done
Wear is done when the six tests in section 6 pass offline, `hero.json` and `summary.json` match the schemas, the BLAST overlay is either real or explicitly failed, and `PHYSICS_LINES.md` cites every physics number in the video.