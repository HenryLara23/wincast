"""ReplayPoller: time-scaled playback, and it never skips the GameEnd snapshot."""

import gzip
import json
import os
import tempfile
import unittest

from tests import ROOT

from wincast.client import DATA, OFFLINE
from wincast.replay import ReplayPoller

GOLDEN = json.loads((ROOT / "tests" / "fixtures" / "golden_game.json").read_text("utf-8"))


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class TestReplayPoller(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".jsonl.gz")
        os.close(fd)
        with gzip.open(self.path, "wt", encoding="utf-8") as fh:
            for i, snap in enumerate(GOLDEN["snapshots"][:5]):     # 2 s apart on the wall clock
                fh.write(json.dumps({"wall_ms": 1000 + 2000 * i, "data": snap}) + "\n")
        self.clock = Clock()

    def tearDown(self):
        os.unlink(self.path)

    def test_plays_in_order_at_speed(self):
        r = ReplayPoller(self.path, speed=2.0, clock=self.clock)
        first = r()
        self.assertEqual(first.kind, DATA)
        self.clock.t += 2.0                      # 4 s of recording at 2x -> third snapshot
        self.assertEqual(r().data["gameData"]["gameTime"],
                         GOLDEN["snapshots"][2]["gameData"]["gameTime"])

    def test_final_snapshot_is_served_even_when_jumping_past_it(self):
        r = ReplayPoller(self.path, speed=1000.0, clock=self.clock)
        r()
        self.clock.t += 60                       # far beyond the end in one poll
        last = r()
        self.assertEqual(last.kind, DATA)
        self.assertEqual(last.data["gameData"]["gameTime"],
                         GOLDEN["snapshots"][4]["gameData"]["gameTime"])
        self.assertEqual(r().kind, OFFLINE)


if __name__ == "__main__":
    unittest.main()
