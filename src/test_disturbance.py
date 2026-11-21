"""Question 4 scenario, event-boundary, and score regression tests."""
import hashlib
import csv
import json
import tempfile
import unittest
from pathlib import Path

from .disturbance import (
    Scenario, _base_policy, _locked_tasks, _score, build_scenarios, run_question4,
)

ROOT = Path(__file__).resolve().parents[1]


class DisturbanceTests(unittest.TestCase):
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

    def test_output_bundle_counts_rows_and_verifies_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            manifest = run_question4(output_dir)
            self.assertEqual(manifest["scenario_count"], 209)
            self.assertEqual(manifest["strategy_rows"], 593)
            for name, expected_hash in manifest["output_sha256"].items():
                actual_hash = hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
                self.assertEqual(actual_hash, expected_hash)
            self.assertNotIn(b"\r\n", (output_dir / "scenarios.json").read_bytes())

            with (output_dir / "event_log.csv").open(encoding="utf-8", newline="") as stream:
                events = list(csv.DictReader(stream))
            with (output_dir / "scenario_scores.csv").open(encoding="utf-8", newline="") as stream:
                scores = list(csv.DictReader(stream))

            for strategy in ("retain_original", "realtime_reschedule", "skip_failed_task"):
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
