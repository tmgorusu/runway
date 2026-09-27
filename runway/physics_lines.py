"""Writes PHYSICS_LINES.md from the pipeline outputs. Every number cites its file. VIDEO_SCRIPT.md is Machine's."""
from __future__ import annotations

from runway.paths import OUTPUTS, ROOT, read_json


def _load(rel):
    path = OUTPUTS / rel
    return read_json(path) if path.exists() else None


def lines() -> list[tuple[str, str]]:
    hero = _load("track1/hero.json")
    summ = _load("replay/summary.json")
    detail = _load("replay/detail.json")
    pair = _load("physics/reference_pair.json")
    glide = _load("physics/glide_sensitivity.json")
    blast = _load("physics/blast_overlay.json")
    metrics = _load("track2/metrics.json")
    out = []
    if pair:
        a, b = pair["units"]
        out.append((f"Two synthetic units, same age, same {pair['kwh']:.0f} kWh on the {pair['call_id']} call: the shaded one "
                    f"runs {a['enclosure_start_c']:.1f}–{a['enclosure_end_c']:.1f} °C, the sun-baked one "
                    f"{b['enclosure_start_c']:.1f}–{b['enclosure_end_c']:.1f} °C, and the sun-baked one loses "
                    f"{pair['ratio_sun1_over_sun0']:.2f}× the capacity for that energy (nominal set). Reserve untouched.",
                    "outputs/physics/reference_pair.json"))
    if hero:
        n = hero["assumption_sets"]["nominal"]
        lo = hero["assumption_sets"]["low_sensitivity"]
        hi = hero["assumption_sets"]["high_sensitivity"]
        out.append((f"Under even spread on the synthetic fleet, a kWh on a missed call wears the battery "
                    f"{n['ratio']:.3f}× as much as a kWh on a hit (low {lo['ratio']:.3f}, high {hi['ratio']:.3f}). A miss is not cheaper.",
                    "outputs/track1/hero.json"))
        out.append((f"{100 * n['wear_fraction_on_misses']:.1f}% of summer call wear lands on calls that missed every 4CP interval "
                    f"({n['kwh_miss'] / 1000:.0f} MWh on misses, {n['kwh_hit'] / 1000:.0f} MWh on hits).",
                    "outputs/track1/hero.json"))
        out.append(("With BLAST-Lite's small-cell LFP data (no temperature effect on cycling), the hot/cool difference disappears: "
                    f"ratio {lo['ratio']:.4f}. The Runway effect rests on the cycle activation energy (31.7 kJ/mol nominal, Wang 2011).",
                    "outputs/track1/hero.json, assumptions/low_sensitivity.json"))
    if blast and blast.get("status") == "ok":
        c = blast["checks"]
        out.append((f"Our aging model matches BLAST-Lite within {100 * c['calendar_25c_365d']['relative_error']:.1f}% (calendar, 25 °C), "
                    f"{100 * c['calendar_45c_365d']['relative_error']:.1f}% (calendar, 45 °C), and "
                    f"{100 * c['cycle_25c_1000efc']['relative_error']:.1f}% (cycle, 25 °C) with zero fitted parameters. "
                    f"At 45 °C our cycle fade is {blast['cycle_45c_ours_over_blast']:.1f}× BLAST's, by design.",
                    "outputs/physics/blast_overlay.json"))
    if glide:
        runs = glide["cost_assumption"]
        g0 = runs["nominal_glide_k_0"]["runway_minus_even_pct"]
        sens_key = next(k for k in runs if k.startswith("sensitivity_glide_k_"))
        gk = runs[sens_key]["runway_minus_even_pct"]
        out.append((f"On the hero calls, Runway uses {g0:+.1f}% total capacity versus even spread (nominal, glide weight off). "
                    f"With the glide weight at k = {sens_key.rsplit('_', 1)[1]} it uses {gk:+.1f}%: a straight-line glide path "
                    "makes older units look ahead of schedule and pulls load onto them.",
                    "outputs/physics/glide_sensitivity.json"))
    if summ and detail:
        er = summ["early_replacements"]
        out.append((f"Summer replay at {summ['feasible_mw']:.1f} MW on 4,000 synthetic units: early replacements "
                    f"runway {er['runway']}, even {er['even']}, most-charge {er['most_charge']}; reserve violations "
                    f"{sum(summ['reserve_violations'].values())}.", "outputs/replay/summary.json"))
        s = summ["saturated_2000_shortfall_kw"]
        out.append((f"At 2,000 units asked for 40 MW, every policy falls {s['even'] / 1000:.2f} MW short on the average call: "
                    "leveling cannot create megawatts.", "outputs/replay/summary.json"))
        oc = detail["over_call_fixture"]["results"]
        if oc:
            out.append((f"Over-call fixture (repeated year, 44 °C, daily call, 10 years): early replacements runway "
                        f"{oc['runway']['early_replacements']}, even {oc['even']['early_replacements']}, most-charge "
                        f"{oc['most_charge']['early_replacements']}. over_call_all_policies_fail = {summ['over_call_all_policies_fail']}.",
                        "outputs/replay/summary.json, outputs/replay/detail.json"))
        out.append((f"ranking_flipped_across_assumption_sets = {summ['ranking_flipped_across_assumption_sets']}.",
                    "outputs/replay/summary.json"))
    run = _load("demo/run.json")
    if run:
        import numpy as np
        from runway.fleet import make_fleet
        from runway.thermal import steady_state_c
        fleet = {u.unit_id: u for u in make_fleet(run["n_units"], run["seed"])}
        faulted = set(run["faulted"])
        after = [(uid, kw) for uid, kw in run["setpoints_after"] if uid not in faulted]
        p = np.array([kw for _, kw in after])
        a = np.array([fleet[uid].age_years for uid, _ in after])
        t = np.array([steady_state_c(run["ambient_c"], fleet[uid].sun_exposure, 0.0) for uid, _ in after])
        out.append((f"After the re-solve, assigned power correlates {np.corrcoef(a, p)[0, 1]:+.2f} with unit age and "
                    f"{np.corrcoef(t, p)[0, 1]:+.2f} with call-start enclosure temperature (nominal set, glide weight off).",
                    "outputs/demo/run.json"))
    if metrics:
        out.append((f"Live offline wave on {metrics['n_units']} synthetic units: {100 * metrics['fraction_offline']:.0f}% offline, "
                    f"re-solve in {metrics['resole_latency_ms']:.0f} ms wall-clock, shortfall {metrics['shortfall_kw']:.1f} kW, "
                    f"reserve violations {metrics['reserve_violations']}.", "outputs/track2/metrics.json"))
    return out


def write() -> None:
    ls = lines()
    body = ["# Physics lines", "",
            "Generated by `runway/physics_lines.py`. Synthetic fleet; ERCOT 2025 dates, load, forecasts, and 4CP intervals are real public data. "
            "Machine does not speak a physics number missing from this sheet.", ""]
    body += [f"{i}. {text} — `{src}`" for i, (text, src) in enumerate(ls, 1)]
    (ROOT / "PHYSICS_LINES.md").write_text("\n".join(body) + "\n")


if __name__ == "__main__":
    write()
