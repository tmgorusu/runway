"""Lumped enclosure thermal model.

C dT/dt = UA (T_amb - T) + solar_gain * sun_exposure + joule * P^2

Inputs are constant over a step, so the exact exponential solution is used and
there is no step-size error. All coefficients are assumptions (assumption files).
"""
from __future__ import annotations

import math

import numpy as np

from runway.assumptions import load

AMBIENT_MIN_C = -30.0
AMBIENT_MAX_C = 55.0


def _check_ambient(ambient_c: float) -> None:
    if not (AMBIENT_MIN_C <= ambient_c <= AMBIENT_MAX_C):
        raise ValueError(f"ambient_c={ambient_c} is outside [{AMBIENT_MIN_C}, {AMBIENT_MAX_C}] °C; is it °F?")


def heat_w(p: dict, ambient_c, sun_exposure, power_kw, enclosure_c):
    return (p["ua_w_per_k"] * (ambient_c - enclosure_c)
            + p["solar_gain_w_at_sun1"] * sun_exposure
            + p["joule_w_per_kw2"] * power_kw * power_kw)


def steady_state_c(ambient_c, sun_exposure, power_kw, assumption: str = "nominal"):
    p = load(assumption)
    return ambient_c + (p["solar_gain_w_at_sun1"] * sun_exposure + p["joule_w_per_kw2"] * power_kw * power_kw) / p["ua_w_per_k"]


def tau_s(assumption: str = "nominal") -> float:
    p = load(assumption)
    return p["thermal_mass_j_per_k"] / p["ua_w_per_k"]


def step_thermal(u, ambient_c: float, power_kw: float, dt_s: float, assumption: str = "nominal") -> float:
    """Enclosure temperature in °C after dt_s.
    Heat terms: exchange with ambient, solar gain scaled by sun_exposure, resistive heating.
    """
    _check_ambient(ambient_c)
    t_ss = steady_state_c(ambient_c, u.sun_exposure, power_kw, assumption)
    return t_ss + (u.enclosure_c - t_ss) * math.exp(-dt_s / tau_s(assumption))


def temps_at(t0_c, ambient_c, sun, power_kw, t_s, assumption: str = "nominal"):
    """Vectorized step_thermal: broadcast over arrays of start temperature, sun, power, and elapsed time."""
    if np.any(np.asarray(ambient_c) < AMBIENT_MIN_C) or np.any(np.asarray(ambient_c) > AMBIENT_MAX_C):
        raise ValueError("ambient_c outside the allowed °C range")
    t_ss = steady_state_c(ambient_c, sun, power_kw, assumption)
    return t_ss + (t0_c - t_ss) * np.exp(-np.asarray(t_s) / tau_s(assumption))
