#!/usr/bin/env python3
"""
The assembly step, shared by both extractors.

`timeline_features.py` and `live_features.py` each know how to read their own
source into per-team numbers. Everything after that -- which differences, which
sums, how role terms are made relative, what the keys are called -- happens here,
once, so the two sides cannot drift apart in the part that is pure arithmetic.

Both sides must supply a team dict with exactly TEAM_KEYS, and role values as
(item_gold, level, cs) per side.
"""

from __future__ import annotations

from . import spec

TEAM_KEYS = tuple(
    [name for name, _ in spec._TEAM_DIFF]
    + [name for name, _ in spec._BUFF]
    + [name for name, _ in spec._DEATH]
)

ROLE_KEYS = ("item_gold_rel", "level_rel")


def assemble(game_time_s: float, blue: dict, red: dict, role_pairs: dict) -> dict:
    """-> the 43 named values in raw units.

    role_pairs maps each role in spec.ROLES to ((blue_gold, blue_level),
    (red_gold, red_level)) or None when the role is missing on a side."""
    for side, name in ((blue, "blue"), (red, "red")):
        missing = [k for k in TEAM_KEYS if k not in side]
        if missing:
            raise KeyError(f"{name} team state is missing {missing}")

    out = {"game_time_s": float(game_time_s)}
    for name, _ in spec._TEAM_DIFF:
        out[f"diff_{name}"] = blue[name] - red[name]
    for name, _ in spec._PACE:
        out[f"sum_{name}"] = blue[name] + red[name]
    for name, _ in spec._BUFF:
        out[f"diff_{name}"] = blue[name] - red[name]
    for name, _ in spec._DEATH:
        out[f"diff_{name}"] = blue[name] - red[name]

    # Role terms are deviations from the team-wide average role difference, so
    # they describe how a lead is DISTRIBUTED and leave "how big is the lead" to
    # the team diffs. Raw role diffs would sum exactly to the team diff.
    raw = {}
    for role in spec.ROLES:
        pair = (role_pairs or {}).get(role)
        raw[role] = tuple(b - r for b, r in zip(*pair)) if pair else None
    present = [v for v in raw.values() if v is not None]
    n = len(ROLE_KEYS)
    mean = ([sum(v[i] for v in present) / len(present) for i in range(n)]
            if present else (0.0,) * n)
    for role in spec.ROLES:
        v = raw[role]
        for i, key in enumerate(ROLE_KEYS):
            out[f"role_{role}_{key}"] = (v[i] - mean[i]) if v is not None else 0.0
    return out


def vector(game_time_s, blue, red, role_pairs):
    return spec.vector(assemble(game_time_s, blue, red, role_pairs))
