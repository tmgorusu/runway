"""Loads assumptions/*.json and enforces the constraints that keep marginal_wear monotone."""
from __future__ import annotations

import json
from functools import lru_cache

from runway.paths import ASSUMPTIONS

SETS = ("nominal", "low_sensitivity", "high_sensitivity")
ALIASES = {"low": "low_sensitivity", "high": "high_sensitivity"}

R_GAS = 8.314462618
T_REF_K = 298.15


def validate(p: dict) -> dict:
    if p["ea_cal_j_per_mol"] < 0 or p["ea_cyc_j_per_mol"] < 0:
        raise ValueError("activation energies must be >= 0 for marginal_wear to stay nondecreasing")
    if p["c_rate_coeff_p6"] <= 0:
        raise ValueError("c_rate_coeff_p6 must be > 0")
    if p["joule_w_per_kw2"] < 0:
        raise ValueError("joule_w_per_kw2 must be >= 0")
    if p["cold_charge_enabled"]:
        raise ValueError("the cold-charge term does not ship")
    return p


@lru_cache(maxsize=None)
def _load(name: str) -> tuple:
    base, *overrides = name.split("+")
    p = json.loads((ASSUMPTIONS / f"{ALIASES.get(base, base)}.json").read_text())
    for item in overrides:
        key, _, val = item.partition("=")
        if key not in p:
            raise KeyError(f"unknown assumption key {key!r}")
        p[key] = type(p[key])(float(val)) if isinstance(p[key], (int, float)) and not isinstance(p[key], bool) else val
    return tuple(sorted(validate(p).items(), key=lambda kv: kv[0]))


def load(name: str = "nominal") -> dict:
    """Load an assumption set. `nominal+glide_k=0` applies a sensitivity override to one key."""
    return dict(_load(name))
