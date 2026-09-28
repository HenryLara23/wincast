#!/usr/bin/env python3
"""
THE feature contract. One list, one order, one set of scales.

Both extractors import this module and neither is allowed its own list:

    lolwp/features/timeline_features.py   training side (match-v5 timelines)
    lolwp/features/live_features.py       serving side (Live Client Data API)

A model bundle records FEATURE_SET_VERSION. The app refuses to load a bundle
whose version does not match this module, because a model fed features in the
wrong order fails silently and no metric catches it.

Bump FEATURE_SET_VERSION on ANY change to names, order, or scales.

Mirror signs
------------
Swapping the two teams must give 1 - P. Every feature is therefore either
symmetric (unchanged by the swap: game time, "pace" totals) or antisymmetric
(negated: every blue-minus-red difference). `mirror()` applies that, which the
net's antisymmetric head and the mirror augmentation both rely on.

    python -m lolwp.features.spec         # print the table and self-test
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FEATURE_SET_VERSION = "1.2.0"

SYM, ANTI = 1, -1


@dataclass(frozen=True)
class Feature:
    name: str
    mirror: int    # +1 symmetric, -1 antisymmetric
    scale: float   # raw value is divided by this before it reaches a model
    group: str


ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")

# CREEP SCORE IS GONE as of 1.2.0, and it is worth saying why in the contract
# itself. A parity capture on 2026-09-19 showed the Live Client API reports
# `creepScore` quantised to multiples of ten -- all 7,570 readings in one game --
# and, for two of the ten players, diverging from match-v5's own end-of-game total
# by 37 and 86 CS. A feature the serving side cannot measure is not a feature.
# Its information largely survives through item gold anyway: CS becomes gold
# becomes items. Dropping it also removed the only interpolated quantity, so every
# remaining feature is now reconstructible exactly at any timestamp.

# Scales are hand-written and frozen here, never fitted from a training set:
# a fitted scaler is one more thing that can differ between training and serving.
# Each is roughly "one meaningful unit" of that quantity.

_TEAM_DIFF = (                      # blue minus red, antisymmetric
    ("item_gold", 1000.0),          # the gold proxy: value of held items
    ("level_sum", 5.0),
    ("kills", 5.0),
    ("deaths", 5.0),                # differs from kills only by executes
    ("assists", 8.0),
    ("alive", 2.0),
    ("towers", 3.0),
    ("inhibs", 1.0),
    ("dragons", 1.0),               # non-elder
    ("elders", 1.0),
    ("heralds", 1.0),
    ("barons", 1.0),
    ("voidgrubs", 3.0),             # HORDE
)

_PACE = (                           # blue plus red, symmetric: how far along the game is
    ("kills", 20.0),
    ("towers", 6.0),
    ("inhibs", 2.0),
    ("dragons", 3.0),
    ("item_gold", 40000.0),
    ("level_sum", 60.0),
)

# Per matched role, RELATIVE to the team-wide difference: the raw role diffs sum
# exactly to the team diff, so including both leaves the team coefficient
# unidentified and a linear model splits the credit arbitrarily (observed
# 2026-09-18: the "value of a gold lead" curve came out non-monotone and partly
# negative). Subtracting the mean role diff makes these pure distribution terms --
# "is the lead on the carry or the support" -- orthogonal to "how big is the lead".
_ROLE = (
    ("item_gold_rel", 800.0),
    ("level_rel", 1.5),
)

_BUFF = (                           # objective state, not cumulative counts
    ("baron_buff_remaining_s", 60.0),
    ("elder_buff_remaining_s", 60.0),
    ("has_soul", 1.0),
    ("inhibs_down_now", 1.0),       # standing destroyed inhibitors, respawn-aware
)

_DEATH = (                          # seconds, not a headcount
    ("respawn_seconds", 60.0),      # summed over this team's dead players
    ("max_respawn_seconds", 30.0),  # longest single timer: the objective window
    ("dead_item_gold", 3000.0),     # five dead supports != five dead carries
)


def _build() -> tuple[Feature, ...]:
    out = [Feature("game_time_s", SYM, 60.0, "time")]
    out += [Feature(f"diff_{n}", ANTI, s, "team_diff") for n, s in _TEAM_DIFF]
    out += [Feature(f"sum_{n}", SYM, s, "pace") for n, s in _PACE]
    for role in ROLES:
        out += [Feature(f"role_{role}_{n}", ANTI, s, "role") for n, s in _ROLE]
    out += [Feature(f"diff_{n}", ANTI, s, "buff") for n, s in _BUFF]
    out += [Feature(f"diff_{n}", ANTI, s, "death") for n, s in _DEATH]
    return tuple(out)


FEATURES = _build()
FEATURE_NAMES = tuple(f.name for f in FEATURES)
N_FEATURES = len(FEATURES)
INDEX = {name: i for i, name in enumerate(FEATURE_NAMES)}
GROUPS = tuple(f.group for f in FEATURES)
MIRROR_SIGNS = np.array([f.mirror for f in FEATURES], dtype=np.float32)
SCALES = np.array([f.scale for f in FEATURES], dtype=np.float32)


def vector(values: dict, dtype=np.float32) -> np.ndarray:
    """Build the canonical vector from a name -> value mapping.

    Raises on a missing or unknown key rather than filling a default. A feature
    silently defaulting to zero on one side and not the other is exactly the
    training/serving drift this module exists to prevent."""
    missing = [n for n in FEATURE_NAMES if n not in values]
    if missing:
        raise KeyError(f"missing {len(missing)} feature(s): {missing[:5]}"
                       f"{' ...' if len(missing) > 5 else ''}")
    unknown = sorted(set(values) - set(FEATURE_NAMES))
    if unknown:
        raise KeyError(f"unknown feature(s) not in the spec: {unknown[:5]}"
                       f"{' ...' if len(unknown) > 5 else ''}")
    return np.fromiter((float(values[n]) for n in FEATURE_NAMES), dtype, N_FEATURES)


def mirror(x: np.ndarray) -> np.ndarray:
    """Swap the two teams. Works on one vector (n,) or a batch (m, n)."""
    return x * MIRROR_SIGNS


def scaled(x: np.ndarray) -> np.ndarray:
    """Raw units -> model units."""
    return x / SCALES


def unscaled(x: np.ndarray) -> np.ndarray:
    return x * SCALES


def group_slice(group: str) -> list[int]:
    return [i for i, g in enumerate(GROUPS) if g == group]


def _self_test() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(7, N_FEATURES)).astype(np.float32)
    assert np.allclose(mirror(mirror(x)), x), "mirror is not an involution"
    assert np.allclose(unscaled(scaled(x)), x, atol=1e-4), "scaling does not round-trip"
    assert len(set(FEATURE_NAMES)) == N_FEATURES, "duplicate feature name"
    assert set(MIRROR_SIGNS) <= {1.0, -1.0}
    assert (SCALES > 0).all()

    v = vector({n: i for i, n in enumerate(FEATURE_NAMES)})
    assert v[INDEX["game_time_s"]] == 0 and v[-1] == N_FEATURES - 1

    for bad, why in (({}, "missing"), ({**{n: 0 for n in FEATURE_NAMES}, "nope": 1}, "unknown")):
        try:
            vector(bad)
        except KeyError:
            pass
        else:
            raise AssertionError(f"vector() accepted {why} keys")
    print(f"spec self-test passed: {N_FEATURES} features, version {FEATURE_SET_VERSION}")


if __name__ == "__main__":
    width = max(len(n) for n in FEATURE_NAMES)
    last = None
    for f in FEATURES:
        if f.group != last:
            n = sum(1 for g in GROUPS if g == f.group)
            print(f"\n[{f.group}]  {n} feature(s)")
            last = f.group
        print(f"  {f.name:<{width}}  {'sym ' if f.mirror == SYM else 'anti'}  /{f.scale:g}")
    print()
    _self_test()
