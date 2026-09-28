import math
import unittest

from tests import ROOT  # noqa: F401  (sets up src/ on the path)

from wincast.smoothing import LogitEMA, logit, sigmoid


class TestLogitEMA(unittest.TestCase):
    def test_first_value_passes_through(self):
        self.assertAlmostEqual(LogitEMA(5).update(0.0, 0.7), 0.7)

    def test_step_reaches_63_percent_of_the_way_in_logit_after_one_tau(self):
        s = LogitEMA(5)
        s.update(0.0, 0.5)
        out = s.update(5.0, 0.9)
        self.assertAlmostEqual(logit(out), (1 - math.exp(-1)) * logit(0.9), places=9)

    def test_paused_clock_does_not_move_the_number(self):
        s = LogitEMA(5)
        s.update(10.0, 0.5)
        self.assertAlmostEqual(s.update(10.0, 0.95), 0.5)

    def test_converges_to_a_constant_input(self):
        s = LogitEMA(5)
        s.update(0.0, 0.2)
        for t in range(2, 120, 2):
            out = s.update(float(t), 0.8)
        self.assertAlmostEqual(out, 0.8, places=6)

    def test_zero_tau_is_off(self):
        s = LogitEMA(0)
        s.update(0.0, 0.2)
        self.assertAlmostEqual(s.update(2.0, 0.8), 0.8)

    def test_clock_going_back_resets(self):
        s = LogitEMA(5)
        s.update(100.0, 0.2)
        self.assertAlmostEqual(s.update(3.0, 0.8), 0.8)

    def test_extremes_stay_finite(self):
        s = LogitEMA(5)
        s.update(0.0, 0.0)
        self.assertTrue(0.0 < s.update(2.0, 1.0) < 1.0)
        self.assertAlmostEqual(sigmoid(logit(0.3)), 0.3)


if __name__ == "__main__":
    unittest.main()
