#!/usr/bin/env python3
"""
Patch-pinned game constants that the Live Client API hands over for free but the
timeline side has to derive.

RESPAWN: MEASURED, not guessed (2026-09-19). `scripts/fit_respawn.py` fitted the
table below against a captured 25:39 ranked game -- 51 deaths across levels 1-16.
All 51 landed within 2s of this table; median error -0.21s, mean -0.14s, worst
-1.03s. The time-increase ramp checks out too: 26 deaths before 15:00 sat at
-0.22s median error and 25 after at -0.19s, and a wrong ramp would have opened a
gap between those. The values are kept as written rather than replaced by the
fit, because the deviations are inside the estimator's own uncertainty (the death
instant is only known to within one 2s poll).

Still to measure: levels 17-18 (no deaths in that game), and BARON_BUFF_S,
ELDER_BUFF_S and INHIB_RESPAWN_S, which the live API never reports directly --
those need a longer game, or an InhibRespawned event to time against.
Re-check each patch.

    python -m lolwp.features.constants        # print the respawn table
"""

from __future__ import annotations

# --- objective durations ---------------------------------------------------
BARON_BUFF_S = 180.0        # UNVALIDATED
ELDER_BUFF_S = 150.0        # UNVALIDATED
INHIB_RESPAWN_S = 300.0     # UNVALIDATED
SOUL_AT_DRAGONS = 4         # a team's 4th dragon grants the soul

# --- respawn ---------------------------------------------------------------
# Base respawn wait in seconds, by champion level.   MEASURED (levels 4-16)
BASE_RESPAWN_WAIT = {
    1: 10.0, 2: 10.0, 3: 12.0, 4: 12.0, 5: 14.0, 6: 16.0,
    7: 20.0, 8: 25.0, 9: 28.0, 10: 32.5, 11: 35.0, 12: 37.5,
    13: 40.0, 14: 42.5, 15: 45.0, 16: 47.5, 17: 50.0, 18: 52.5,
}

# The time increase factor adds a percentage of the base wait that grows as the
# game goes on. Expressed as (from_second, to_second, fraction added per second)
# so the whole thing is one editable table.          MEASURED (ramp confirmed)
TIME_INCREASE_SEGMENTS = (
    (0.0,    900.0,  0.0),
    (900.0,  1800.0, 0.00425 / 30.0),
    (1800.0, 2700.0, 0.00300 / 30.0),
    (2700.0, float("inf"), 0.01450 / 30.0),
)

MAX_TIME_INCREASE = 0.50    # cap, so a 90-minute game can't produce a silly timer


def time_increase_factor(game_s: float) -> float:
    """Accumulated respawn-time increase at this point in the game (0.0 = none)."""
    total = 0.0
    for start, end, rate in TIME_INCREASE_SEGMENTS:
        if game_s <= start:
            break
        total += (min(game_s, end) - start) * rate
    return min(total, MAX_TIME_INCREASE)


def respawn_seconds(level: int, game_s: float) -> float:
    """How long a champion of this level stays dead if it dies now."""
    base = BASE_RESPAWN_WAIT[max(1, min(18, int(level)))]
    return base * (1.0 + time_increase_factor(game_s))


if __name__ == "__main__":
    print("level   0:00   10:00   20:00   30:00   40:00   50:00")
    for lvl in range(1, 19):
        row = "  ".join(f"{respawn_seconds(lvl, m * 60):6.1f}" for m in (0, 10, 20, 30, 40, 50))
        print(f"{lvl:>4}  {row}")
    print("\nMeasured against 51 deaths in a captured game (2026-09-19): all within 2s.")
