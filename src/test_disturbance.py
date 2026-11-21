"""Question 4 scenario, event-boundary, and score regression tests."""
# OpenAI Codex 0.169.0-alpha.11.1（GPT-6，OpenAI）辅助程序复查与测试。
# 工具版本发布于 2026-11-11 UTC，使用于 2026-11-21。范围与来源见 yyh/B题代码复核.md。
import hashlib
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .assignment import candidate_stations
from .disturbance import (
    Scenario, _base_policy, _locked_tasks, _reschedule, _score, _skip_failed_task,
    _local_repair, _preservable_prefixes, _trigger_tasks, _triggered_reschedule, build_scenarios, run_question4,
)
from .routing import Router
from .timeline import evaluate_schedule, vehicle_state_at

ROOT = Path(__file__).resolve().parents[1]


class DisturbanceTests(unittest.TestCase):
    def test_preservation_requires_the_predecessors_of_both_partners(self):
        queues = {"a": ["P", "T"], "b": ["Q", "T"], "c": ["P"], "d": ["Q"]}
        choices = set(_preservable_prefixes(queues, {"P", "Q", "T"}, set()))
        self.assertEqual(choices, set(map(frozenset, [(), ("P",), ("Q",), ("P", "Q"), ("P", "Q", "T")])))
        # P has started; Q is blocked. T still depends on Q.
        self.assertEqual(list(_preservable_prefixes(queues, {"T"}, {"P"})), [frozenset()])

    def test_local_repair_at_extra_recovery_times(self):
        router, pools = Router(), candidate_stations()
        for t in (6.5, 9.25, 12.5, 27.5):
            for car in self.plan["orders"]:
                with self.subTest(recovery=t, failed=car):
                    scenario = Scenario("extra", failed_vehicle=car, recovery_time_s=t)
                    base, _ = _triggered_reschedule(self.plan, scenario, router, pools)
                    local, log = _local_repair(self.plan, scenario, router, pools)
                    self.assertLessEqual((-local["successful_alerts"], local["t_last_s"]),
                                         (-base["successful_alerts"], base["t_last_s"]))
                    self.assertEqual([e["time_s"] for e in log if e["event"] == "reschedule_decision"], [t])
                    self.assertFalse(any(e["event"] == "command_cancelled" and e["time_s"] < t for e in log))

    def test_trigger_uses_the_failed_queue_and_strictly_prior_start_events(self):
        scenario = Scenario("boundary", failed_vehicle="V2", recovery_time_s=14)
        events = [{"vehicle_id": "V2", "event": "observation_start", "task_id": task, "time_s": at}
                  for task, at in (("D", 4.99), ("A", 5.0), ("C", 5.01))]
        plan = {"orders": {"V2": ["D", "A", "C"]}, "vehicle_events": events}
        self.assertEqual(_trigger_tasks(plan, scenario), ["A", "C"])
        plan["vehicle_events"] = events[:1]
        self.assertEqual(_trigger_tasks(plan, scenario), ["A", "C"])
        both = Scenario("combined", new_alert=("new", (0, 40, 50)), failed_vehicle="V2")
        self.assertEqual(_trigger_tasks(plan, both), ["A", "C", "new"])

    def test_trigger_at_additional_recovery_times(self):
        router, pools = Router(), candidate_stations()
        for t in (6.5, 9.25, 12.5, 27.5):
            for car in self.plan["orders"]:
                with self.subTest(recovery=t, failed=car):
                    s = Scenario("extra", failed_vehicle=car, recovery_time_s=t)
                    score, log = _triggered_reschedule(self.plan, s, router, pools)
                    decisions = [e for e in log if e["event"] == "reschedule_decision"]
                    self.assertEqual(len(decisions), 1)
                    decision, = decisions
                    self.assertEqual(decision["time_s"], t)
                    self.assertFalse(any(e["event"] == "command_cancelled" and e["time_s"] < t for e in log))
                    pending = {task for task in self.plan["orders"][car]
                               if self.plan["start_times"][task] >= 5}
                    self.assertEqual(set(decision["reason"]), pending)
                    self.assertEqual(decision["decision"], "repair" if pending else "continue")
                    self.assertEqual(score["successful_alerts"], 3)
                    if not pending:
                        self.assertEqual(score["task_start_times_s"], self.plan["start_times"])
                        self.assertFalse(any(e["event"] == "command_cancelled" for e in log))

    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((ROOT / "results" / "question2" / "original_plan.json")
                              .read_text(encoding="utf-8"))

    def test_all_single_pairwise_and_triple_scenarios_exist(self):
        scenarios = build_scenarios()
        sets = {scenario.disturbance_set if hasattr(scenario, "disturbance_set") else
                ("+".join(label for label, present in (
                    ("new_alert", scenario.has_new_alert), ("failure", bool(scenario.failed_vehicle)),
                    ("communication_outage", scenario.has_outage)) if present))
                for scenario in scenarios}
        self.assertEqual(len(scenarios), 209)
        self.assertIn("new_alert", sets)
        self.assertIn("failure", sets)
        self.assertIn("communication_outage", sets)
        self.assertIn("new_alert+failure", sets)
        self.assertIn("new_alert+communication_outage", sets)
        self.assertIn("failure+communication_outage", sets)
        self.assertIn("new_alert+failure+communication_outage", sets)
        self.assertTrue(any(s.outage_interpretation == "duration"
                            and s.recovery_time_s == 35.0 for s in scenarios))

    def test_only_strictly_pre_disturbance_starts_are_locked(self):
        plan = {"start_times": {"T1": 4.999, "T2": 5.0, "T3": 5.001},
                "orders": {"V1": ["T1"], "V2": ["T1"], "V3": ["T2"],
                           "V4": ["T2"], "V5": ["T3"]}}
        locked = _locked_tasks(plan, Scenario("boundary"))
        self.assertEqual(locked, {"T1"})

    def test_failure_blocking_respects_vehicle_queue_order(self):
        orders = {car: list(tasks) for car, tasks in self.plan["orders"].items()}
        stations = {task: dict(crew) for task, crew in self.plan["stations"].items()}
        orders["V3"].remove("T3")
        orders["V1"].append("T3")
        stations["T3"].pop("V3")
        stations["T3"]["V1"] = (15.0, 14.0)
        plan = evaluate_schedule(orders, stations, Router())
        self.assertTrue(plan["feasible"], plan["reason"])
        self.assertAlmostEqual(plan["start_times"]["T2"], 6.949461242856117)
        self.assertAlmostEqual(plan["start_times"]["T3"], 16.737812470817566)

        later_failure, _ = _base_policy(
            plan, Scenario("later-failure", failed_vehicle="V2"), "retain_original")
        self.assertEqual(later_failure["task_start_times_s"]["T2"],
                         plan["start_times"]["T2"])
        self.assertIsNone(later_failure["task_start_times_s"]["T3"])
        self.assertEqual(later_failure["successful_alerts"], 2)

        outage_failure, _ = _base_policy(
            plan, Scenario("earlier-failure", failed_vehicle="V4",
                           recovery_time_s=30.0), "retain_original")
        self.assertIsNotNone(outage_failure["task_start_times_s"]["T1"])
        self.assertIsNone(outage_failure["task_start_times_s"]["T2"])
        self.assertIsNone(outage_failure["task_start_times_s"]["T3"])
        self.assertEqual(outage_failure["successful_alerts"], 1)

    def test_window_score_uses_closed_endpoint_and_missing_alert_sentinel(self):
        bounded = _score({"T1": -1e-6, "T2": 0.0, "T3": 60.0,
                          "T4": 60.000001}, 4, ("T1", "T2", "T3", "T4"))
        self.assertEqual(bounded["successful_alerts"], 2)
        self.assertEqual(bounded["completion_rate"], 0.5)
        self.assertEqual(bounded["t_last_s"], 60.0)
        incomplete = _score({"T1": None}, 1, ("T1",))
        self.assertEqual(incomplete["completion_rate"], 0.0)
        self.assertEqual(incomplete["t_last_s"], 60.0)
        late = _score({"T1": 60.000001}, 1, ("T1",))
        self.assertEqual(late["completion_rate"], 0.0)

    def test_new_alert_is_counted_but_not_started_by_original_plan(self):
        scenario = Scenario("new", new_alert=("N1", (0.0, 30.0, 50.0)))
        score, _ = _base_policy(self.plan, scenario, "retain_original")
        self.assertEqual(score["alert_count"], 4)
        self.assertEqual(score["successful_alerts"], 3)
        self.assertEqual(score["completion_rate"], 0.75)
        self.assertIsNone(score["task_start_times_s"]["N1"])

    def test_skip_keeps_in_progress_healthy_assignments(self):
        # V5's only assignment, T1, already started. T2 and T3 need no new commands.
        scenario = Scenario("failure-V5", failed_vehicle="V5")
        score, events = _skip_failed_task(self.plan, scenario, Router())
        self.assertEqual(score["task_start_times_s"], self.plan["start_times"])
        for car, task in (("V1", "T2"), ("V2", "T3"), ("V4", "T2")):
            departures = [e["time_s"] for e in events if e["vehicle_id"] == car
                          and e["task_id"] == task and e["event"] == "departure"]
            self.assertEqual(departures, [0.0])

    def test_skip_releases_blocked_successor_at_recovery(self):
        orders = {car: list(tasks) for car, tasks in self.plan["orders"].items()}
        stations = {task: dict(crew) for task, crew in self.plan["stations"].items()}
        orders["V3"].remove("T3")
        orders["V1"].append("T3")
        stations["T3"].pop("V3")
        stations["T3"]["V1"] = (15.0, 14.0)
        plan = evaluate_schedule(orders, stations, Router())
        scenario = Scenario("failure+outage", failed_vehicle="V4", recovery_time_s=30.0)
        score, events = _skip_failed_task(plan, scenario, Router())
        self.assertEqual(score["successful_alerts"], 2)
        self.assertIsNone(score["task_start_times_s"]["T2"])
        self.assertGreater(score["task_start_times_s"]["T3"], 30.0)
        for car in ("V1", "V2"):
            departures = [e["time_s"] for e in events if e["vehicle_id"] == car
                          and e["task_id"] == "T3" and e["event"] == "departure"]
            if car == "V2":
                self.assertEqual(departures, [0.0])
            else:
                self.assertGreater(min(departures), 30.0)

    def test_interrupted_slew_and_replanned_poses_are_logged(self):
        scenario = Scenario("new", new_alert=("N1", (0.0, 30.0, 50.0)))
        score, events = _reschedule(self.plan, scenario, "realtime_reschedule",
                                    Router(), candidate_stations())
        pose = vehicle_state_at(self.plan, "V2", 5.0)
        begin, end = [e for e in events if e["vehicle_id"] == "V2"
                      and e["event"] in ("interrupt_reset_start", "interrupt_reset_end")]
        self.assertEqual((begin["time_s"], begin["pan_deg"], begin["tilt_deg"]),
                         (5.0, pose["pan_deg"], pose["tilt_deg"]))
        self.assertAlmostEqual(end["time_s"], 5.0 + max(abs(pose["pan_deg"]),
                                                        abs(pose["tilt_deg"])) / 30.0)
        self.assertEqual((end["pan_deg"], end["tilt_deg"]), (0.0, 0.0))
        for event in events:
            if event["event"] == "departure" and event["vehicle_id"] == "V2" and event["time_s"] >= 5:
                self.assertGreaterEqual(event["time_s"], end["time_s"])
        for start in (e for e in events if e["event"] == "observation_start"):
            self.assertAlmostEqual(start["time_s"], score["task_start_times_s"][start["task_id"]])
            self.assertGreater(start["tilt_deg"], 0.0)

    def test_unavailable_candidates_score_the_executable_prefix(self):
        scenario = Scenario("no-options", new_alert=("N1", (0.0, 30.0, 50.0)))
        with patch("src.disturbance._mission_options", return_value=[]):
            score, _ = _reschedule(self.plan, scenario, "realtime_reschedule", Router(), {})
        self.assertEqual(score["successful_alerts"], 1)
        self.assertEqual(score["completion_rate"], 0.25)
        self.assertEqual(score["t_last_s"], 60.0)
        self.assertEqual(score["task_start_times_s"]["T1"], self.plan["start_times"]["T1"])

    def test_output_bundle_counts_rows_and_verifies_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            manifest = run_question4(output_dir)
            self.assertEqual(manifest["scenario_count"], 209)
            self.assertEqual(manifest["strategy_rows"], 1011)
            for name, expected_hash in manifest["output_sha256"].items():
                actual_hash = hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
                self.assertEqual(actual_hash, expected_hash)
            self.assertNotIn(b"\r\n", (output_dir / "scenarios.json").read_bytes())

            with (output_dir / "event_log.csv").open(encoding="utf-8", newline="") as stream:
                events = list(csv.DictReader(stream))
            with (output_dir / "scenario_scores.csv").open(encoding="utf-8", newline="") as stream:
                scores = list(csv.DictReader(stream))

            for strategy in ("retain_original", "realtime_reschedule", "skip_failed_task", "triggered_reschedule", "local_repair"):
                failed_vehicle_events = [event for event in events
                                         if event["scenario_id"] == "failure-V3"
                                         and event["strategy"] == strategy
                                         and event["vehicle_id"] == "V3"]
                self.assertTrue(any(event["event"] == "failure"
                                    and event["time_s"] == "5.0"
                                    for event in failed_vehicle_events))
                self.assertFalse(any(float(event["time_s"]) > 5.0
                                     for event in failed_vehicle_events))
                score = next(row for row in scores
                             if row["scenario_id"] == "failure-V3"
                             and row["strategy"] == strategy)
                self.assertIsNotNone(json.loads(score["task_start_times_s"])["T1"])

            self.assertTrue(any(event["scenario_id"] == "new-N1"
                                and event["strategy"] == "realtime_reschedule"
                                and event["vehicle_id"] == "V1"
                                and event["task_id"] == "T2"
                                and event["event"] == "departure"
                                and event["time_s"] == "0.0" for event in events))

            for car in ("V2", "V3"):
                task_events = [event for event in events
                               if event["scenario_id"] == "failure-V1"
                               and event["strategy"] == "skip_failed_task"
                               and event["task_id"] == "T3"
                               and event["vehicle_id"] == car]
                event_names = {event["event"] for event in task_events}
                self.assertTrue({"arrival", "slew_start", "slew_end", "observation_start"}
                                <= event_names)
                slew_start = next(event for event in task_events
                                  if event["event"] == "slew_start")
                reset_end = next(event for event in task_events
                                 if event["event"] == "reset_end")
                self.assertEqual(float(slew_start["pan_deg"]), 0.0)
                self.assertEqual(float(reset_end["tilt_deg"]), 0.0)

            skipped_new_alert = next(row for row in scores
                                     if row["scenario_id"] == "new-N1-failure-V1"
                                     and row["strategy"] == "skip_failed_task")
            starts = json.loads(skipped_new_alert["task_start_times_s"])
            self.assertIn("N1", starts)
            self.assertIsNone(starts["N1"])


if __name__ == "__main__":
    unittest.main()
