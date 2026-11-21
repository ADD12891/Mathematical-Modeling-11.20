"""第二问（1）误差模型、可行站对与最优性证书回归。"""
import json
from math import cos, radians, sin, sqrt
import unittest

import numpy as np

from .config import ALERTS, target_position
from .geometry import check_station
from .localization import is_nondegenerate_pair, pair_metrics, solve_localization
from .routing import Router


class PairMetricsTests(unittest.TestCase):
    def test_orthogonal_covariance(self):
        metrics = pair_metrics((0, 0, 35), (10, 0), (0, 10))
        sigma = radians(1)
        np.testing.assert_allclose(metrics["covariance_m2"], np.eye(2)*(10*sigma)**2)
        self.assertAlmostEqual(metrics["baseline_m"], sqrt(200))
        self.assertAlmostEqual(metrics["rms_m"], 0.246826830, places=8)

    def test_obtuse_angles_and_unequal_ranges(self):
        for r1, r2, angle, baseline, rms in (
            (10, 10, 120., 17.320508, 0.285011073),
            (8, 20, 110., 23.946742, 0.400083408),
        ):
            with self.subTest(angle=angle):
                a = (r1, 0.)
                b = (r2*cos(radians(angle)), r2*sin(radians(angle)))
                result = pair_metrics((0, 0, 35), a, b)
                self.assertAlmostEqual(result["angle_deg"], angle)
                self.assertAlmostEqual(result["baseline_m"], baseline, places=6)
                self.assertAlmostEqual(result["rms_m"], rms, places=8)
                self.assertAlmostEqual(result["rms_m"], result["closed_form_rms_m"])
                reverse = pair_metrics((0, 0, 35), b, a)
                np.testing.assert_allclose(reverse["covariance_m2"], result["covariance_m2"])

    def test_coincident_collinear_and_nearly_collinear(self):
        t = (0, 0, 35)
        for b in ((0, 0), (-5, 0), (10, 0), (20, 0)):
            self.assertFalse(is_nondegenerate_pair(t, (10, 0), b))
            self.assertIsNone(pair_metrics(t, (10, 0), b)["rms_m"])
        b = (10, 1e-5)
        self.assertTrue(is_nondegenerate_pair(t, (10, 0), b))
        near = pair_metrics(t, (10, 0), b)
        self.assertGreater(near["condition_number"], 1e6)
        self.assertAlmostEqual(near["rms_m"]/near["closed_form_rms_m"], 1.)


class LocalizationSolutionTests(unittest.TestCase):
    def test_routes_bound_margin_and_grid_check(self):
        report = solve_localization(Router())
        json.dumps(report, allow_nan=False)
        optimal = report["optimal"]
        self.assertTrue(report["certificate"]["bound_attained"])
        self.assertAlmostEqual(optimal["metrics"]["rms_m"], 0.143623357923, places=11)
        self.assertAlmostEqual(optimal["metrics"]["baseline_m"], 8.2290122485, places=9)
        self.assertAlmostEqual(optimal["metrics"]["angle_deg"], 90.)
        for row, vehicle, expected_t, gap in zip(optimal["assignments"], ("V3", "V5"),
                                                  (5.369802, 13.881594), (0.604320, 0.818790)):
            self.assertEqual(row["vehicle"], vehicle)
            self.assertAlmostEqual(row["travel_time_s"], expected_t, places=6)
            self.assertAlmostEqual(row["elevation_deg"], 80.)
            self.assertTrue(row["sight_clear"])
            self.assertEqual(len(row["segments"]), 1)
            self.assertTrue(row["segments"][0]["passable"])
            self.assertAlmostEqual(row["segments"][0]["building_clearance_m"], gap, places=6)
        # 正西站沿目标方向再走 1 cm，交会角未变，但仰角超限。
        west_x, west_y = optimal["assignments"][0]["station_xy_m"]
        inward = check_station(target_position(ALERTS["T2"]), (west_x + .01, west_y))
        self.assertFalse(inward.elevation_ok)
        self.assertAlmostEqual(inward.elevation_deg, 80.01684, places=5)
        margin = report["margin_example"]
        self.assertAlmostEqual(margin["metrics"]["rms_m"], 0.150963941425, places=11)
        self.assertAlmostEqual(margin["rms_increase_percent"], 5.11, places=2)
        self.assertTrue(all(a["travel_time_s"] <= 30 for a in margin["assignments"]))
        grid = report["search"]["grid"]["metrics"]["rms_m"]
        refined = report["search"]["refined"]["metrics"]["rms_m"]
        self.assertLess(refined, grid)
        self.assertGreaterEqual(refined + 1e-9, report["certificate"]["rms_lower_bound_m"])


if __name__ == "__main__":
    unittest.main()
