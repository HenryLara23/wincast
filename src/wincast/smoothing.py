"""Smoothing the displayed number.

An exponential moving average over GAME time, done in log-odds rather than on
the percentage. Why each part:

  game time    a pause stops the clock, so the number stops moving too; a slow
               or skipped poll gets exactly the catch-up it should.
  log-odds     90% -> 99% is as big a change as 50% -> 90%; averaging the raw
               percentage would drag decisive swings near the edges.
  tau ~5 s     long enough to hide single-poll jitter (an item swap, a death
               timer ticking), short enough that a teamfight shows within a poll
               or two. tau = 0 turns smoothing off.
"""

import math

EPS = 1e-6


def logit(p):
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z))


class LogitEMA:
    def __init__(self, tau_s: float = 5.0):
        self.tau_s = float(tau_s)
        self.reset()

    def reset(self):
        self._z = None
        self._t = None

    def update(self, t: float, p: float) -> float:
        z = logit(p)
        if self._z is None or self.tau_s <= 0 or self._t is None or t < self._t:
            self._z, self._t = z, t          # first value, smoothing off, or clock went back
            return sigmoid(z)
        dt = t - self._t
        alpha = 1.0 - math.exp(-dt / self.tau_s)
        self._z += alpha * (z - self._z)
        self._t = t
        return sigmoid(self._z)
