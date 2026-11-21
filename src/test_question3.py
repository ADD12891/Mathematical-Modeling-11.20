"""第三问回归。用旋转扇区两侧的高斯半平面概率独立核对原始角度模型。"""
import math
import unittest
from statistics import NormalDist

import numpy as np

from .bearing_error import probability_model
from .question3 import probability_at_distance, solve_question3


def sector_reference(sigma, gimbal_std_deg):
    """本题阈值邻域的几何参考解。σ≤2 m 时，背向半平面的交叠概率可忽略。"""
    nodes, weights = np.polynomial.legendre.leggauss(96)
    integral = 0.0
    for node, weight in zip(nodes, weights):
        z = 12 * node
        error = gimbal_std_deg * z
        halfplanes = [40 * math.sin(math.radians(5 + sign * error)) / sigma
                      for sign in (-1, 1)]
        # 条件于执行角误差，两个越界半平面的概率之和给出失败概率。
        success = 1 - sum(0.5 * math.erfc(x / math.sqrt(2)) for x in halfplanes)
        integral += weight * math.exp(-z*z/2) * success
    return 12 * integral / math.sqrt(2 * math.pi)


class Question3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = probability_model(80)
        cls.summary, cls.convergence, cls.contributions = solve_question3()

    def test_independent_sector_model(self):
        for sigma in (0.3, 1.1, 1.63, 1.78, 1.9):
            for fn, gimbal_std in ((self.model.command, 0), (self.model.actual, 1)):
                with self.subTest(sigma=sigma, gimbal_std=gimbal_std):
                    self.assertAlmostEqual(fn(sigma), sector_reference(sigma, gimbal_std), delta=1e-8)

    def test_thresholds_and_last_passing_centimeter(self):
        for metric, expected, centimeter in (("command", 73.9360655962, 73.93),
                                             ("actual", 66.8372584279, 66.83)):
            limit = self.summary["limits"][metric]
            self.assertAlmostEqual(limit["critical_distance_m"], expected, delta=1e-7)
            self.assertEqual(limit["max_passing_cm_grid_m"], centimeter)
            self.assertAlmostEqual(limit["next_cm_grid_m"], centimeter + .01)
            self.assertGreaterEqual(limit["probability_at_max_cm_grid"], .95)
            self.assertLess(limit["probability_at_next_cm_grid"], .95)
            self.assertLess(limit["nonlinear_probability_at_first_order_limit"], .95)

    def test_orders_and_unrounded_80m_constraint(self):
        for metric, fn in (("command", self.model.command), ("actual", self.model.actual)):
            rows = [r for r in self.convergence if r["metric"] == metric]
            self.assertEqual([r["order"] for r in rows], [24, 48, 80])
            self.assertLess(abs(rows[-1]["change_from_previous_order_m"]), 1e-5)
            domain = self.summary["limits"][metric]["parameter_domain_at_80m"]
            self.assertGreaterEqual(fn(domain["passing_sigma_m"]), .95)
            self.assertLess(fn(domain["failing_sigma_m"]), .95)
            b = domain["passing_b_if_a_is_nominal"]
            key = metric + "_meets_requirement"
            self.assertTrue(probability_at_distance(self.model, 80, .3, b)[key])
            self.assertTrue(probability_at_distance(self.model, 80, .3, b - 1e-7)[key])
            self.assertFalse(probability_at_distance(self.model, 80, .3, b + 1e-7)[key])
        nominal = self.summary["nominal_at_80m"]
        self.assertAlmostEqual(nominal["command_probability"], .9334732296185, delta=1e-8)
        self.assertAlmostEqual(nominal["actual_probability"], .9150682311608, delta=1e-8)

    def test_variance_and_linear_reference(self):
        for row, position_pct in zip(self.contributions, (15.5874, 71.2860, 88.1049)):
            self.assertAlmostEqual(100 * row["actual_position_share"], position_pct, delta=.0001)
            self.assertAlmostEqual(sum(row[k] for k in row if k.endswith("_share")), 1)
            self.assertAlmostEqual(row["actual_zero_offset_share"] / row["actual_control_share"],
                                   .8**2 / .6**2)
        limits = self.summary["limits"]
        self.assertAlmostEqual(limits["actual"]["first_order_critical_distance_m"], 66.922299239)

    def test_zero_error_and_invalid_inputs(self):
        result = probability_at_distance(self.model, 80, a=0, b=0)
        self.assertEqual(result["command_probability"], 1)
        self.assertAlmostEqual(result["actual_probability"], 2 * NormalDist().cdf(5) - 1)
        for args in ((-1, .3, .02), (80, -1, .02), (80, .3, -.02), (math.nan, .3, .02)):
            with self.assertRaises(ValueError):
                probability_at_distance(self.model, *args)

    def test_broad_error_limit(self):
        # 极大各向同性位置误差的方位近似均匀。加独立角噪声后仍为均匀方位。
        for fn in (self.model.command, self.model.actual):
            self.assertAlmostEqual(fn(1e100), 10/360, delta=1e-8)
            self.assertAlmostEqual(fn(2.0 - 1e-7), fn(2.0 + 1e-7), delta=1e-6)


if __name__ == "__main__":
    unittest.main()
