"""运行 python -m unittest -v src.test_question1。预期值来自独立几何核验。"""
import math
import unittest

import numpy as np

from .config import BUILDINGS, t1_position
from .geometry import check_station, intersects_building, is_station_observable
from .question1 import choose_station, solve_question1


class GeometryTests(unittest.TestCase):
    def test_field_edge_and_building_tangency(self):
        target = t1_position()
        for x, expected in ((1.5, True), (1.4999999, False), (98.5, True), (98.5000001, False)):
            self.assertEqual(bool(check_station(target, (x, 50)).in_field), expected)
        scale = 1 + np.array([-1e-6, 0, 1e-6])
        result = check_station(target, (34 + 0.9*scale, 16 - 1.2*scale))
        np.testing.assert_array_equal(result.observable, [False, True, True])
        self.assertEqual(result.clearance_m[1], 0)

    def test_closed_sight_segment(self):
        b1 = BUILDINGS[0]
        # 分别从 B1 的屋顶棱下方、正好擦棱、上方经过。
        for height, blocked in ((37.98, True), (38., True), (38.02, False)):
            self.assertEqual(intersects_building((27, 21, height), (17, 21), b1), blocked)
        # 恰好接触西南顶点、沿南面通过，以及平行且在南面外经过。
        self.assertTrue(intersects_building((27, 21, 38), (17, 11), b1))
        self.assertTrue(intersects_building((38, 16, 2), (18, 16), b1))
        self.assertFalse(intersects_building((38, 15.999, 2), (18, 15.999), b1))
        # 无穷射线将穿过 B1，但相机至目标的有限线段在楼前结束。
        self.assertFalse(intersects_building((20, 21, 2), (18, 21), b1))

    def test_t1_roof_edge(self):
        y = 1233 / 17 + np.array([-1e-6, 0, 1e-6])
        np.testing.assert_array_equal(is_station_observable(t1_position(), (50, y)),
                                      [False, False, True])

    def test_elevation_limits(self):
        target = t1_position()
        radius = 43 / math.tan(math.radians(80))
        for delta, expected in ((-1e-4, False), (0, True), (1e-4, True)):
            station = (target[0] + radius + delta, target[1])
            self.assertEqual(is_station_observable(target, station), expected)
        # 固定车心与水平距离，按 -10° 反推目标高度，另取刚超限的一点。
        z = 2 - 8 * math.tan(math.radians(10))
        for height, expected in ((z, True), (z - 1e-6, False)):
            check = check_station((18, 10, height), (10, 10))
            self.assertEqual(check.elevation_ok, expected)

    def test_tied_stations(self):
        tx, ty, height = t1_position()
        r = (height - 2) / math.tan(math.radians(80))
        east = (tx + r, ty)
        northeast = (tx + r/math.sqrt(2), ty + r/math.sqrt(2))
        for order in ((east, northeast), (northeast, east)):
            self.assertTrue(all(is_station_observable(t1_position(), p) for p in order))
            self.assertEqual(choose_station(t1_position(), order), east)


class SolutionTests(unittest.TestCase):
    def test_grid_and_continuous_solution(self):
        result, rows, mask = solve_question1()
        self.assertEqual([r["observable_points"] for r in rows], [22432, 89839, 358908])
        self.assertEqual(mask.shape, (800, 800))
        self.assertEqual(rows[-1]["area_estimate_m2"], 5607.9375)
        self.assertTrue(result["observable"])
        self.assertEqual(result["optimality"], "global_lower_bound_attained")
        self.assertAlmostEqual(result["horizontal_distance_m"], 7.582060170464, places=10)
        self.assertAlmostEqual(result["best_refined_distance_m"], result["lower_bound_m"], places=7)
        self.assertAlmostEqual(result["quality_per_m"], 0.131890274875, places=10)
        self.assertAlmostEqual(result["elevation_deg"], 80., places=10)


if __name__ == "__main__":
    unittest.main()
