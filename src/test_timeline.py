"""时间线回归：同步、单车释放、连续回放、成环与显式行程约束。"""
import math
import unittest
from unittest.mock import patch

from . import timeline
from .config import t1_position


class StraightRoad:
    @staticmethod
    def route(start, end):
        return (start, end)


class TimelineTests(unittest.TestCase):
    def setUp(self):
        # 空旷位置中，两条行程恰为 3 m、6 m，目标方位恰为 90°、30°。
        self.west = (55 - 5 * math.sqrt(3), 80)
        self.starts = {"V1": (52, 75), "V2": (self.west[0], 74), "V3": (59, 87)}
        self.targets = {"T1": (55, 85, 2)}
        self.orders = {"V1": ["T1"], "V2": ["T1"], "V3": []}
        self.stations = {"T1": {"V1": (55, 75), "V2": self.west}}
        context = patch.multiple(timeline, ALERTS=self.targets, VEHICLES=self.starts,
                                 target_position=lambda alert: alert)
        context.start()
        self.addCleanup(context.stop)

    def solve(self, router=None, max_leg_m=None):
        return timeline.evaluate_schedule(self.orders, self.stations,
                                          router or StraightRoad(), max_leg_m)

    @staticmethod
    def times(result, vehicle, task):
        return {e["event"]: e["time_s"] for e in result["vehicle_events"]
                if e["vehicle_id"] == vehicle and e["task_id"] == task}

    def test_synchronization_individual_reset_and_idle(self):
        result = self.solve()
        self.assertTrue(result["feasible"], result["reason"])
        a, b = (self.times(result, v, "T1") for v in ("V1", "V2"))
        for actual, expected in (
            (a["arrival"], 1), (b["arrival"], 2), (a["slew_end"], 4), (b["slew_end"], 3),
            (b["wait_partner"], 3), (a["observation_start"], 4), (b["observation_start"], 4),
            (a["observation_end"], 9), (b["observation_end"], 9),
            (a["reset_end"], 12), (b["reset_end"], 10),
            (result["latest_start_s"], 4), (result["total_wait_s"], 1),
            (result["total_distance_m"], 9),
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(timeline.vehicle_state_at(result, "V2", 3.5)["stage"], "WAIT_PARTNER")
        self.assertEqual(timeline.vehicle_state_at(result, "V3", 5)["stage"], "IDLE")

    def test_reused_vehicle_can_depart_at_own_reset_end(self):
        self.targets["T2"] = (55, 90, 2)
        self.stations["T2"] = {"V2": self.west, "V3": (59, 87)}
        self.orders["V2"].append("T2")
        self.orders["V3"].append("T2")
        result = self.solve()
        self.assertTrue(result["feasible"], result["reason"])
        self.assertAlmostEqual(self.times(result, "V2", "T1")["next_departure"], 10)
        self.assertAlmostEqual(self.times(result, "V2", "T2")["departure"], 10)
        self.assertEqual(self.times(result, "V3", "T2")["departure"], 0)
        self.assertEqual(timeline.vehicle_state_at(result, "V2", 10)["stage"], "SLEW")
        self.assertEqual(timeline.vehicle_state_at(result, "V1", 10)["stage"], "RESET")

    def test_unrelated_teams_operate_concurrently(self):
        self.starts["V4"] = (60, 90)
        self.targets["T2"] = (60, 88, 2)
        self.stations["T2"] = {"V3": self.starts["V3"], "V4": self.starts["V4"]}
        self.orders.update(V3=["T2"], V4=["T2"])
        result = self.solve()
        self.assertTrue(result["feasible"], result["reason"])
        self.assertAlmostEqual(result["start_times"]["T2"], 3)
        self.assertAlmostEqual(result["latest_start_s"], 4)

    def test_cycle_rejected_before_routing(self):
        self.targets["T2"] = (55, 90, 2)
        self.stations["T2"] = dict(self.stations["T1"])
        self.orders.update(V1=["T1", "T2"], V2=["T2", "T1"])
        self.assertIn("成环", self.solve()["reason"])

    def test_geometry_and_travel_constraints(self):
        self.assertTrue(self.solve(max_leg_m=6)["feasible"])
        self.assertIn("直线距离下界", self.solve(max_leg_m=5.9)["reason"])
        self.stations["T1"]["V2"] = (55, 76)
        self.assertIn("唯一交点", self.solve()["reason"])
        self.stations["T1"]["V2"] = (28, 21)
        self.assertIn("不可观测", self.solve()["reason"])

    def test_reject_path_crossing_building(self):
        class BlockedRoad:
            @staticmethod
            def route(start, end):
                return (start, (28, 21), end)
        self.assertIn("中段侵入", self.solve(BlockedRoad())["reason"])

    def test_polyline_progress_and_unproven_leg_limit(self):
        class RoadWithCorner:
            @staticmethod
            def route(start, end):
                return (start, (52, 76), end) if start == (52, 75) else (start, end)
        road = RoadWithCorner()
        result = self.solve(road)
        state = timeline.vehicle_state_at(result, "V1", 0.5)
        self.assertEqual(state["stage"], "TRAVEL")
        self.assertAlmostEqual(state["x_m"], 52 + 1.5/math.sqrt(10))
        self.assertAlmostEqual(state["y_m"], 76 - 0.5/math.sqrt(10))
        self.assertIn("需加密", self.solve(road, max_leg_m=3.5)["reason"])

    def test_t1_half_turn_snapshot(self):
        tx, ty, height = t1_position()
        r = (height - 2) / math.tan(math.radians(80))
        self.targets["T1"] = (tx, ty, height)
        self.starts.update(V1=(88, 68), V2=(50, 93))
        self.stations["T1"] = {"V1": (tx + r, ty), "V2": (tx, ty + r)}
        result = self.solve()
        self.assertTrue(result["feasible"], result["reason"])
        state = timeline.vehicle_state_at(result, "V1", 5)
        self.assertEqual(state["stage"], "SLEW")
        self.assertAlmostEqual(state["pan_deg"], -103.272734, places=6)
        self.assertAlmostEqual(state["tilt_deg"], 80)
        reset_time = self.times(result, "V1", "T1")["reset_start"]
        reset = timeline.vehicle_state_at(result, "V1", reset_time + 3)
        self.assertAlmostEqual(reset["pan_deg"], 90)
        self.assertAlmostEqual(reset["tilt_deg"], 0)


if __name__ == "__main__":
    unittest.main()
