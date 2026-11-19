"""Seeded parameter randomisation.

A spec maps a unit-suffixed parameter name to a distribution:
    {dist: uniform, low: .., high: ..}
    {dist: normal, mean: .., std: .., low: .., high: ..}      (truncated by resampling)
    {dist: lognormal, median: .., sigma_nd: ..}
    {dist: choice, values: [..]}
    {dist: const, value: ..}   or a bare number
``sample(spec, rng)`` draws one value per name, in sorted name order, so adding a new
parameter does not change the draws of the others only if its name sorts last -- callers
that need stable streams give each trial its own Generator seeded from the trial seed.
"""
from __future__ import annotations

import math

import numpy as np


def sample_one(d, rng: np.random.Generator):
    if not isinstance(d, dict):
        return d
    kind = d.get("dist", "const")
    if kind == "const":
        return d["value"]
    if kind == "uniform":
        return float(rng.uniform(d["low"], d["high"]))
    if kind == "normal":
        lo, hi = d.get("low", -math.inf), d.get("high", math.inf)
        for _ in range(1000):
            x = float(rng.normal(d["mean"], d["std"]))
            if lo <= x <= hi:
                return x
        return float(min(max(d["mean"], lo), hi))
    if kind == "lognormal":
        return float(d["median"] * math.exp(rng.normal(0.0, d["sigma_nd"])))
    if kind == "choice":
        vals = d["values"]
        return vals[int(rng.integers(len(vals)))]
    raise ValueError(f"unknown distribution {kind!r}")


def sample(spec: dict, rng: np.random.Generator) -> dict:
    return {k: sample_one(spec[k], rng) for k in sorted(spec)}
