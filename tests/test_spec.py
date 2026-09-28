import unittest

import numpy as np

from lolwp.features import spec


class TestSpec(unittest.TestCase):
    def test_shape_and_uniqueness(self):
        self.assertEqual(len(spec.FEATURE_NAMES), spec.N_FEATURES)
        self.assertEqual(len(set(spec.FEATURE_NAMES)), spec.N_FEATURES)
        self.assertEqual(spec.FEATURE_NAMES[0], "game_time_s")

    def test_mirror_is_an_involution(self):
        x = np.random.default_rng(0).normal(size=(5, spec.N_FEATURES)).astype(np.float32)
        np.testing.assert_allclose(spec.mirror(spec.mirror(x)), x)

    def test_time_and_pace_are_symmetric(self):
        for name in ("game_time_s", "sum_kills", "sum_item_gold"):
            self.assertEqual(spec.FEATURES[spec.INDEX[name]].mirror, spec.SYM)

    def test_differences_are_antisymmetric(self):
        for name in ("diff_item_gold", "diff_respawn_seconds", "role_TOP_level_rel"):
            self.assertEqual(spec.FEATURES[spec.INDEX[name]].mirror, spec.ANTI)

    def test_vector_rejects_missing_and_unknown(self):
        full = {n: 0.0 for n in spec.FEATURE_NAMES}
        spec.vector(full)                                    # fine
        with self.assertRaises(KeyError):
            spec.vector({k: v for k, v in list(full.items())[:-1]})
        with self.assertRaises(KeyError):
            spec.vector({**full, "not_a_feature": 1.0})

    def test_scaling_round_trips(self):
        x = np.arange(spec.N_FEATURES, dtype=np.float32)
        np.testing.assert_allclose(spec.unscaled(spec.scaled(x)), x, rtol=1e-5)
