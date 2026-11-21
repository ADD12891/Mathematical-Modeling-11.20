"""从导出的五车时间表复算第二问；不调用搜索器的目标函数或时间线回放。"""
import csv
import json
import math
import tempfile
import unittest
from itertools import combinations, permutations, product
from pathlib import Path

from . import config
from .geometry import is_station_observable
from .question2 import run_assignment, text_hash
from .routing import Router, segment_clearance


class Question2OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.out = Path(tmp.name)
        run_assignment(Router(), cls.out)

    def read_table(self, name):
        with (self.out / name).open(encoding="utf8", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_reconstruct_all_four_event_tables(self):
        legs = self.read_table("vehicle_tasks.csv")
        comparison = self.read_table("comparison.csv")
        self.assertEqual(len(comparison), 4)
        for summary in comparison:
            scenario, strategy = summary["scenario"], summary["strategy"]
            events = self.read_table(f"{scenario}_{strategy}_events.csv")
            self.assertEqual({e["vehicle_id"] for e in events}, set(config.VEHICLES))
            rows = [r for r in legs if (r["scenario"], r["strategy"]) == (scenario, strategy)]
            previous = dict.fromkeys(config.VEHICLES, 0.)
            mileage = dict.fromkeys(config.VEHICLES, 0.)
            position = dict(config.VEHICLES)
            waiting = 0.
            for r in rows:
                car, task = r["vehicle_id"], r["task_id"]
                if task == "IDLE":
                    continue
                times = {e["event"]: float(e["time_s"]) for e in events
                         if (e["vehicle_id"], e["task_id"]) == (car, task)}
                path = json.loads(r["route_xy_m"])
                point = (float(r["station_x_m"]), float(r["station_y_m"]))
                self.assertEqual(tuple(path[0]), position[car])
                self.assertEqual(tuple(path[-1]), point)
                length = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
                self.assertTrue(all(segment_clearance(a, b) >= 0 for a, b in zip(path, path[1:])))
                if scenario == "limited":
                    self.assertLessEqual(length, 90 + 1e-9)
                target = config.target_position(config.ALERTS[task])
                self.assertTrue(is_station_observable(target, point))
                dx, dy = target[0] - point[0], target[1] - point[1]
                pan = math.degrees(math.atan2(dy, dx))
                tilt = math.degrees(math.atan2(target[2]-2, math.hypot(dx, dy)))
                turn = max(abs(pan), abs(tilt)) / 30
                self.assertAlmostEqual(times["departure"], previous[car])
                self.assertAlmostEqual(times["arrival"], previous[car] + length / 3)
                self.assertAlmostEqual(times["slew_start"], times["arrival"])
                self.assertAlmostEqual(times["slew_end"], times["arrival"] + turn)
                self.assertAlmostEqual(times["observation_end"], times["observation_start"] + 5)
                self.assertEqual(times["reset_start"], times["observation_end"])
                self.assertAlmostEqual(times["reset_end"], times["observation_end"] + turn)
                for event, t in times.items():
                    if event+"_s" in r:
                        self.assertEqual(float(r[event+"_s"]), t)
                waiting += times["observation_start"] - times["slew_end"]
                mileage[car] += length
                self.assertAlmostEqual(float(r["cumulative_distance_m"]), mileage[car])
                previous[car], position[car] = times["reset_end"], point
            starts = []
            for task in config.ALERTS:
                crew = [r for r in rows if r["task_id"] == task]
                self.assertEqual(len({r["vehicle_id"] for r in crew}), 2)
                start = max(float(r["slew_end_s"]) for r in crew)
                self.assertTrue(all(float(r["observation_start_s"]) == start for r in crew))
                self.assertEqual(start, float(summary[task + "_start_s"]))
                starts.append(start)
            self.assertEqual(max(starts), float(summary["latest_start_s"]))
            self.assertAlmostEqual(sum(mileage.values()), float(summary["total_distance_m"]))
            self.assertAlmostEqual(waiting, float(summary["total_wait_s"]))

    def test_search_result_and_original_plan(self):
        rows = self.read_table("comparison.csv")
        results = {(r["scenario"], r["strategy"]): float(r["latest_start_s"]) for r in rows}
        self.assertAlmostEqual(results["main", "nearest"], 10.96898424449)
        self.assertAlmostEqual(results["main", "optimized"], 10.38710108549)
        self.assertEqual(results["limited", "optimized"], results["main", "optimized"])
        plan = json.loads((self.out / "original_plan.json").read_text(encoding="utf8"))
        self.assertEqual(plan["latest_start_s"], results["main", "optimized"])
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf8"))
        for name, digest in manifest["output_sha256"].items():
            self.assertEqual(text_hash(self.out / name), digest)

    def test_finite_pool_deadline_with_optimistic_travel(self):
        # 独立的标量枚举，把所有路线放宽为直线。在放宽模型亦不可行的截止时刻，
        # 原问题必不可行。已导出方案为可行上界，两者共同核查搜索结果。
        sites = json.loads((self.out / "candidates.json").read_text())
        options = {}
        for task, points in sites.items():
            tx, ty, tz = config.target_position(config.ALERTS[task])
            options[task] = []
            for x, y in points:
                dx, dy = tx-x, ty-y
                r = math.hypot(dx, dy)
                turn = math.degrees(max(abs(math.atan2(dy, dx)), math.atan2(tz-2, r))) / 30
                options[task].append(((x, y), turn, dx/r, dy/r))

        def can_finish(positions, releases, order, deadline):
            task, *later = order
            choices = []
            for origin, release in zip(positions, releases):
                available = []
                for site, turn, ux, uy in options[task]:
                    ready = release + math.dist(origin, site)/3 + turn
                    if ready <= deadline:
                        available.append((site, ready, turn, ux, uy))
                choices.append(available)
            for i, j in combinations(range(len(positions)), 2):
                for a, b in product(choices[i], choices[j]):
                    if abs(a[3]*b[4] - a[4]*b[3]) <= 16 * math.ulp(1.):
                        continue
                    if not later:
                        return True
                    end = max(a[1], b[1]) + 5
                    new_positions, new_releases = positions.copy(), releases.copy()
                    new_positions[i], new_positions[j] = a[0], b[0]
                    new_releases[i], new_releases[j] = end + a[2], end + b[2]
                    if can_finish(new_positions, new_releases, later, deadline):
                        return True
            return False

        latest = json.loads((self.out / "original_plan.json").read_text())["latest_start_s"]
        starts = list(config.VEHICLES.values())
        for delta, expected in ((-1e-7, False), (1e-7, True)):
            feasible = any(can_finish(starts, [0.] * len(starts), order, latest + delta)
                           for order in permutations(options))
            self.assertEqual(feasible, expected)


if __name__ == "__main__":
    unittest.main()
