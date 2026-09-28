"""Re-score a real (anonymised) game and compare with what the model repo's own
code produced for it at sync time. Catches a drifted copy, a numpy change that
moves the numbers, or a fallback model that doesn't match the code."""

import json
import unittest

import numpy as np

from tests import ROOT

from lolwp.features import live_features as live
from lolwp.model.bundle import Model
from lolwp.store.items_db import ItemDB


class TestGoldenGame(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = json.loads((ROOT / "tests" / "fixtures" / "golden_game.json").read_text("utf-8"))
        cls.model = Model.load(str(ROOT / cls.g["model"]))
        cls.db = ItemDB.load(version=cls.g["items_version"])

    def test_same_features_and_win_chance(self):
        self.assertGreaterEqual(len(self.g["snapshots"]), 5)
        for i, (snap, exp) in enumerate(zip(self.g["snapshots"], self.g["expected"])):
            with self.subTest(snapshot=i):
                ok, _ = live.game_check(snap)
                self.assertEqual(ok, exp["ok"])
                x = live.extract(snap, self.db)
                np.testing.assert_allclose(np.asarray(x, dtype=float), exp["x"],
                                           rtol=0, atol=1e-9)
                self.assertAlmostEqual(self.model.predict_one(x), exp["p_blue"], places=9)

    def test_no_real_names_in_fixture(self):
        for snap in self.g["snapshots"]:
            for p in snap["allPlayers"]:
                self.assertRegex(p.get("riotId", ""), r"^(Blue|Red)[1-5]#NA1$")


if __name__ == "__main__":
    unittest.main()
