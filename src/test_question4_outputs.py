"""从发布的逐车事件重算每个场景的成绩，并校验单场景调试入口。"""
# OpenAI Codex 0.169.0-alpha.11.1（GPT-6，OpenAI）辅助程序复查与测试。
# 工具版本发布于 2026-11-11 UTC，使用于 2026-11-21。范围与来源见 yyh/B题代码复核.md。
import csv
import hashlib
import json
import tempfile
import unittest
from collections import Counter, defaultdict
from functools import lru_cache
from math import atan2, degrees, hypot
from pathlib import Path

from . import config
from .disturbance import OUT, ROOT, _load_frozen_plan, run_question4
from .geometry import is_station_observable
from .localization import is_nondegenerate_pair
from .routing import route_length, segment_clearance


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


@lru_cache(maxsize=None)
def station_pose(target, x, y):
    dx, dy = target[0] - x, target[1] - y
    return (bool(is_station_observable(target, (x, y))), degrees(atan2(dy, dx)),
            degrees(atan2(target[2] - config.CAMERA_HEIGHT, hypot(dx, dy))))


class PublishedTimelines(unittest.TestCase):
    def test_scores_follow_paired_observations(self):
        scenarios = {s["scenario_id"]: s for s in
                     json.loads((OUT / "scenarios.json").read_text(encoding="utf-8"))}
        grouped = defaultdict(list)
        for event in read_rows(OUT / "event_log.csv"):
            grouped[event["scenario_id"], event["strategy"]].append(event)
        scores = read_rows(OUT / "scenario_scores.csv")
        policies = {(s["scenario_id"], strategy) for s in scenarios.values()
                    for strategy in (["retain_original", "realtime_reschedule"] +
                                     (["skip_failed_task"] if s["failed_vehicle"] else []))}
        self.assertEqual(Counter((r["scenario_id"], r["strategy"]) for r in scores),
                         Counter({key: 1 for key in policies}))
        self.assertEqual(set(grouped), policies)
        self.assertEqual(len(policies), 593)
        reference = json.loads((ROOT / "results/question2/original_plan.json").read_text(encoding="utf-8"))
        def signature(e):
            return e["vehicle_id"], e["task_id"], e["event"], float(e["time_s"])
        prefix = Counter(signature(e) for e in reference["vehicle_events"] if e["time_s"] < 5)
        for row in scores:
            key = row["scenario_id"], row["strategy"]
            with self.subTest(scenario=key):
                scenario = scenarios[key[0]]
                alerts = dict(config.ALERTS)
                if scenario["new_alert"]:
                    task, alert = scenario["new_alert"]
                    alerts[task] = alert
                started = defaultdict(list)
                arrived, ready = {}, {}
                self.assertEqual(Counter(signature(e) for e in grouped[key] if float(e["time_s"]) < 5),
                                 prefix)
                for event in grouped[key]:
                    car, time = event["vehicle_id"], float(event["time_s"])
                    action, task = event["event"], event["task_id"]
                    if car == scenario["failed_vehicle"] and event["event"] != "failure":
                        self.assertLess(time, 5.0)
                    if action in ("reschedule_decision", "command_cancelled") or task not in config.ALERTS and task:
                        self.assertGreaterEqual(time, scenario["recovery_time_s"] or 5.0)
                    if action in ("departure", "command_cancelled"):
                        arrived.pop(car, None)
                        ready.pop(car, None)
                    elif action == "arrival":
                        arrived[car] = task
                    elif action == "slew_end":
                        self.assertEqual(arrived.get(car), task)
                        ready[car] = task
                    elif action == "observation_start":
                        self.assertEqual(ready.get(car), task)
                        started[task].append(event)
                reported = json.loads(row["task_start_times_s"])
                self.assertEqual(set(reported), set(alerts))
                self.assertEqual(set(started), {t for t in alerts if reported[t] is not None})
                times = []
                for task, pair in started.items():
                    self.assertEqual(len(pair), 2)
                    first, second = pair
                    self.assertNotEqual(first["vehicle_id"], second["vehicle_id"])
                    self.assertEqual(first["partner_id"], second["vehicle_id"])
                    self.assertEqual(second["partner_id"], first["vehicle_id"])
                    self.assertEqual(first["time_s"], second["time_s"])
                    t = float(first["time_s"])
                    self.assertAlmostEqual(reported[task], t, places=9)
                    target = config.target_position(alerts[task])
                    stations = []
                    for event in pair:
                        x, y = float(event["x_m"]), float(event["y_m"])
                        visible, pan, tilt = station_pose(target, x, y)
                        self.assertTrue(visible)
                        self.assertAlmostEqual((float(event["pan_deg"]) - pan + 180) % 360 - 180,
                                               0.0, places=8)
                        self.assertAlmostEqual(float(event["tilt_deg"]), tilt, places=8)
                        stations.append((x, y))
                    self.assertTrue(is_nondegenerate_pair(target, *stations))
                    if 0 <= t <= 60:
                        times.append(t)
                total = len(alerts)
                self.assertEqual(int(row["successful_alerts"]), len(times))
                self.assertEqual(int(row["alert_count"]), total)
                self.assertAlmostEqual(float(row["completion_rate"]), len(times) / total)
                self.assertAlmostEqual(float(row["t_last_s"]),
                                       max(times) if len(times) == total else 60.0)

    def test_departure_routes_are_machine_readable_and_feasible(self):
        checked_routes = {}
        for event in read_rows(OUT / "event_log.csv"):
            if event["event"] != "departure":
                continue
            raw = event["route_xy_m"]
            if raw not in checked_routes:
                path = json.loads(raw)
                for point in path:
                    self.assertTrue(all(config.VEHICLE_RADIUS <= v <=
                                        config.FIELD_SIZE - config.VEHICLE_RADIUS for v in point))
                for a, b in zip(path, path[1:]):
                    self.assertGreaterEqual(segment_clearance(a, b), -config.BODY_TOL_M)
                checked_routes[raw] = (tuple(path[0]), route_length(path))
            origin, length = checked_routes[raw]
            self.assertEqual(origin, (float(event["x_m"]), float(event["y_m"])))
            self.assertAlmostEqual(length, float(event["leg_distance_m"]), places=8)
            self.assertEqual((float(event["pan_deg"]), float(event["tilt_deg"])), (0.0, 0.0))

    def test_frozen_plan_accepts_checkout_newlines_and_rejects_edits(self):
        source = ROOT / "results" / "question2"
        filename = "original_plan.json"
        raw = (source / filename).read_text(encoding="utf-8").encode()
        with tempfile.TemporaryDirectory() as name:
            out = Path(name)
            (out / "manifest.json").write_bytes((source / "manifest.json").read_bytes())
            for ending in (b"\n", b"\r\n"):
                (out / filename).write_bytes(raw.replace(b"\n", ending))
                plan, digest = _load_frozen_plan(out)
                self.assertTrue(plan["feasible"])
                self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
            (out / filename).write_bytes(raw + b" ")
            with self.assertRaisesRegex(ValueError, "冻结清单"):
                _load_frozen_plan(out)

    def test_single_scenario_matches_published_results(self):
        scenario = "new-N1-failure-V1-outage-rec-30"
        with tempfile.TemporaryDirectory() as name:
            out = Path(name)
            manifest = run_question4(out, scenario)
            self.assertEqual((manifest["scenario_count"], manifest["strategy_rows"]), (1, 3))
            for filename in ("scenario_scores.csv", "event_log.csv"):
                published = [row for row in read_rows(OUT / filename)
                             if row["scenario_id"] == scenario]
                actual = read_rows(out / filename)
                # A single scenario can omit columns used only in other scenarios.
                self.assertEqual([{k: v for k, v in r.items() if v} for r in actual],
                                 [{k: v for k, v in r.items() if v} for r in published])
