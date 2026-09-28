"""Headless mode: output format and a full replay through the engine."""

import unittest

from tests import ROOT  # noqa: F401

from wincast import headless
from wincast.session import Update


class TestHeadless(unittest.TestCase):
    def test_player_line(self):
        out = headless.line(Update("in_game", game_time=125.0, team="CHAOS",
                                   p_mine=0.62, p_mine_raw=0.64))
        self.assertTrue(out.startswith(" 2:05  you (red)  62.0%"))
        self.assertIn("raw  64.0%", out)

    def test_spectator_line_shows_both_sides(self):
        out = headless.line(Update("in_game", game_time=125.0, team=None,
                                   p_mine=0.62, p_mine_raw=0.62))
        self.assertIn("blue  62.0%", out)
        self.assertIn("red  38.0%", out)


if __name__ == "__main__":
    unittest.main()
