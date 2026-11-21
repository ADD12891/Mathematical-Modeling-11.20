"""用 python -m unittest -v src.test_routing 复核整段路线与车体净空。"""
from math import dist, radians, tan
import unittest

from .config import ALERTS, VEHICLES, target_position
from .routing import Router, route_length, segment_clearance


class RoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.router = Router()

    def test_direct_route_and_field_boundary(self):
        a, b = (1.5, 1.5), (98.5, 1.5)
        self.assertEqual(self.router.route(a, b), (a, b))
        self.assertEqual(route_length(self.router.route(a, b)), 97)
        self.assertEqual(self.router.route(a, a), (a, a))
        self.assertEqual(route_length(self.router.route(a, a)), 0)
        self.assertIsNone(self.router.route((1.5 - 1e-6, 1.5), b))
        self.assertIsNone(self.router.route(a, (98.5 + 1e-6, 1.5)))
        self.assertIsNone(self.router.route(a, (28, 20)))

    def test_edge_and_corner_tangencies(self):
        # B1 西侧竖直路线与建筑平行，车体侧面恰好外切。
        a, b = (20.5, 16), (20.5, 26)
        self.assertEqual(segment_clearance(a, b), 0)
        self.assertEqual(self.router.route(a, b), (a, b))
        # 法向量 (0.6,-0.8)，路线中点是东南圆角的切点。
        a, b = (33.7, 13.9), (36.1, 15.7)
        self.assertEqual(segment_clearance(a, b), 0)
        self.assertEqual(self.router.route(a, b), (a, b))
        a_in, b_in = (a[0] - 6e-7, a[1] + 8e-7), (b[0] - 6e-7, b[1] + 8e-7)
        self.assertAlmostEqual(segment_clearance(a_in, b_in), -1e-6, places=10)
        detour = self.router.route(a_in, b_in)
        self.assertIsNotNone(detour)
        self.assertGreater(len(detour), 2)

    def test_crossing_without_any_illegal_endpoint(self):
        a, b = (18, 21), (38, 21)
        self.assertGreater(segment_clearance(a, a), 0)
        self.assertGreater(segment_clearance(b, b), 0)
        self.assertEqual(segment_clearance(a, b), -1.5)
        path = self.router.route(a, b)
        self.assertEqual((path[0], path[-1]), (a, b))
        self.assertGreater(route_length(path), dist(a, b))
        self.assertLess(route_length(path), 28)
        for start, end in zip(path, path[1:]):
            self.assertGreaterEqual(segment_clearance(start, end), 0)
        self.assertEqual(self.router.route(b, a), path[::-1])
        self.assertEqual(Router().route(b, a), path[::-1])

    def test_t2_nominal_and_interior_stations(self):
        x, y, height = target_position(ALERTS["T2"])
        for angle, v3_time, v3_gap, v5_time, v5_gap in (
            (80., 5.369802, 0.604320, 13.881594, 0.818790),
            (79.5, 5.424529, 0.765838, 13.846957, 1.116188),
        ):
            radius = (height - 2) / tan(radians(angle))
            for vehicle, station, time, gap in (
                ("V3", (x - radius, y), v3_time, v3_gap),
                ("V5", (x, y + radius), v5_time, v5_gap),
            ):
                with self.subTest(angle=angle, vehicle=vehicle):
                    start = VEHICLES[vehicle]
                    path = self.router.route(start, station)
                    self.assertEqual(path, (start, station))
                    self.assertAlmostEqual(route_length(path) / 3, time, places=6)
                    self.assertAlmostEqual(segment_clearance(start, station), gap, places=6)


if __name__ == "__main__":
    unittest.main()
