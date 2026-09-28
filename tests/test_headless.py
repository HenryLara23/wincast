"""The headless loop's pieces: the Summoner's Rift gate, side detection, output."""

import json
import unittest

from tests import ROOT

from wincast import headless


class TestHeadless(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fx = ROOT / "tests" / "fixtures"
        cls.golden = json.loads((fx / "golden_game.json").read_text("utf-8"))
        cls.aram = json.loads((fx / "sample_howling_abyss.json").read_text("utf-8"))
        cls.model = headless.load_model()
        cls.db = headless.load_items(cls.model, cls.golden["items_version"])

    def test_aram_is_refused(self):
        ok, why = headless.live.game_check(self.aram)
        self.assertFalse(ok)
        self.assertIn("Summoner's Rift", why)

    def test_scores_a_real_game_for_the_player_on_this_pc(self):
        snap = self.golden["snapshots"][-1]
        t, p_blue, team = headless.score(snap, self.model, self.db)
        self.assertIn(team, ("ORDER", "CHAOS"))
        self.assertTrue(0.0 < p_blue < 1.0)
        self.assertIn("you (", headless.line(t, p_blue, team))

    def test_spectator_line_shows_both_sides(self):
        out = headless.line(125.0, 0.62, None)
        self.assertTrue(out.startswith(" 2:05"))
        self.assertIn("blue  62.0%", out)
        self.assertIn("red  38.0%", out)


if __name__ == "__main__":
    unittest.main()
