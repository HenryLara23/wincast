"""Play a saved capture back as if it were live, at any speed.

Each poll returns the snapshot that would be current `elapsed x speed` into the
recording, so the overlay can be watched on a real game without League running:

    python -m wincast --replay capture.jsonl.gz --speed 20
"""

from __future__ import annotations

import bisect
import glob
import gzip
import json
import time

from .client import OFFLINE, Poll


class ReplayPoller:
    def __init__(self, pattern: str, speed: float = 10.0, clock=time.monotonic):
        files = sorted(glob.glob(pattern))
        if not files:
            raise FileNotFoundError(f"no capture matches {pattern}")
        self.walls, self.data = [], []
        with gzip.open(files[-1], "rt", encoding="utf-8") as fh:
            for raw in fh:
                rec = json.loads(raw)
                self.walls.append(rec["wall_ms"])
                self.data.append(rec.get("data"))
        if not self.walls:
            raise ValueError(f"{files[-1]} is empty")
        self.speed = max(float(speed), 0.01)
        self.clock = clock
        self._t0 = None
        self._served_last = False

    def __call__(self) -> Poll:
        now = self.clock()
        if self._t0 is None:
            self._t0 = now
        target = self.walls[0] + (now - self._t0) * 1000.0 * self.speed
        i = max(bisect.bisect_right(self.walls, target) - 1, 0)
        if i == len(self.walls) - 1:
            # Serve the final snapshot at least once however fast the replay runs:
            # it is the one that carries the GameEnd event.
            if self._served_last and target > self.walls[-1] + 3000:
                return Poll(OFFLINE, detail="end of replay")      # client gone
            self._served_last = True
        return Poll.of(self.data[i])
