"""LFP capacity-fade model.

Calendar: BLAST-Lite 1.1.1 Lfp_Gr_250AhPrismatic form, k_cal(T, SOC) * t^pcal,
re-referenced at 25 °C and SOC 1.0 (sourced). Integrated exactly over the step.
Cycle: that model's magnitude, EFC exponent, and linear C-rate prefactor
(sourced), with its inverse temperature term replaced by an Arrhenius term
whose activation energy comes from the assumption file (nominal: Wang et al.
2011). Cycle hardening is linearized at the step start so wear is linear in
throughput, which keeps marginal_wear nondecreasing in power.
"""
from __future__ import annotations

import math

import numpy as np

from runway.assumptions import R_GAS, T_REF_K, load
from runway.contracts import CAPACITY_KWH

DAYS_PER_YEAR = 365.25


def ua(soc):
    """Graphite anode potential (V) versus SOC, from BLAST-Lite `_get_Ua` (sourced)."""
    xa = 8.5e-3 + np.asarray(soc, dtype=float) * (0.78 - 8.5e-3)
    return (0.6379 + 0.5416 * np.exp(-305.5309 * xa)
            + 0.044 * np.tanh(-(xa - 0.1958) / 0.1088)
            - 0.1978 * np.tanh((xa - 1.0571) / 0.0854)
            - 0.6875 * np.tanh((xa + 0.0117) / 0.0529)
            - 0.0175 * np.tanh((xa - 0.5692) / 0.0875))


_UA1 = float(ua(1.0))


def k_cal(p: dict, temp_k, soc):
    """Calendar coefficient, capacity fraction per day^pcal."""
    return (p["kcal_ref"]
            * np.exp(-p["ea_cal_j_per_mol"] / R_GAS * (1.0 / temp_k - 1.0 / T_REF_K))
            * np.exp(p["ua_coeff_k_per_v"] * (ua(soc) / temp_k - _UA1 / T_REF_K)))


def k_cyc(p: dict, temp_k, c_rate):
    """Cycle coefficient, capacity fraction per EFC^pcyc."""
    base = p["c_rate_coeff_p4"] + p["c_rate_coeff_p5"] * p["dod_ref"]
    f_c = (base + p["c_rate_coeff_p6"] * c_rate) / (base + p["c_rate_coeff_p6"] * p["c_rate_ref"])
    return p["kcyc_ref"] * f_c * np.exp(-p["ea_cyc_j_per_mol"] / R_GAS * (1.0 / temp_k - 1.0 / T_REF_K))


def power_increment(t0, dt, expo):
    """(t0 + dt)^expo - t0^expo, accurate when dt << t0."""
    t0 = np.asarray(t0, dtype=float)
    safe = np.where(t0 > 0, t0, 1.0)
    grown = safe ** expo * np.expm1(expo * np.log1p(dt / safe))
    return np.where(t0 > 0, grown, np.asarray(dt, dtype=float) ** expo)


def cycle_hardening(p: dict, efc):
    return p["pcyc"] * np.maximum(efc, p["efc_floor"]) ** (p["pcyc"] - 1.0)


def unit_efc(u, p: dict) -> float:
    """Cumulative equivalent full cycles. Machine's UnitState carries none, so fall back to
    metadata['efc'], then to age_years * efc_per_year."""
    efc = getattr(u, "efc", None)
    if efc is None:
        efc = (getattr(u, "metadata", None) or {}).get("efc")
    return float(u.age_years * p["efc_per_year"] if efc is None else efc)


def _kelvin(celsius):
    return np.asarray(celsius, dtype=float) + 273.15


def _age_days(u):
    return u.age_years * DAYS_PER_YEAR


def _efc_step(power_kw, dt_s):
    return abs(power_kw) * dt_s / 3600.0 / (2.0 * CAPACITY_KWH)


def _c_rate(power_kw):
    return abs(power_kw) / CAPACITY_KWH


def age(u, enclosure_c, power_kw, dt_s, assumption="nominal") -> float:
    """Capacity fraction lost during dt_s. Positive degrades the cell.
    Terms: Arrhenius calendar at the call-start SOC, Arrhenius cycle, linear C-rate.
    Calendar hardening uses u.age_years; cycle hardening uses the unit's cumulative EFC.
    """
    p = load(assumption)
    temp_k = _kelvin(enclosure_c)
    calendar = k_cal(p, temp_k, u.soc) * power_increment(_age_days(u), dt_s / 86400.0, p["pcal"])
    cycle = k_cyc(p, temp_k, _c_rate(power_kw)) * cycle_hardening(p, unit_efc(u, p)) * _efc_step(power_kw, dt_s)
    return float(calendar + cycle)


def age_arrays(p: dict, enclosure_c, power_kw, dt_s, soc, age_years, efc):
    """Vectorized `age`. Broadcasts over every array argument."""
    temp_k = _kelvin(enclosure_c)
    power_kw = np.abs(np.asarray(power_kw, dtype=float))
    calendar = k_cal(p, temp_k, soc) * power_increment(np.asarray(age_years) * DAYS_PER_YEAR, dt_s / 86400.0, p["pcal"])
    efc_step = power_kw * dt_s / 3600.0 / (2.0 * CAPACITY_KWH)
    cycle = k_cyc(p, temp_k, power_kw / CAPACITY_KWH) * cycle_hardening(p, efc) * efc_step
    return calendar + cycle


def initial_health(age_years: float, term_years: int, assumption="nominal") -> float:
    """Remaining capacity fraction at the start of a replay.
    age() integrated at the assumption file's enclosure temperature and SOC, with
    efc_per_year of cycling at the reference C-rate.
    """
    p = load(assumption)
    temp_k = _kelvin(p["initial_health_enclosure_c"])
    days = age_years * DAYS_PER_YEAR
    calendar = k_cal(p, temp_k, p["initial_health_soc"]) * days ** p["pcal"]
    efc = age_years * p["efc_per_year"]
    cycle = k_cyc(p, temp_k, p["c_rate_ref"]) * efc ** p["pcyc"]
    return float(1.0 - calendar - cycle)
