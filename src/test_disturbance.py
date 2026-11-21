"""Question 4 scenario, event-boundary, and score regression tests."""
import hashlib
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
        complete = _score({"T1": 60.0}, 1, ("T1",))
        self.assertEqual(complete["completion_rate"], 1.0)
        self.assertEqual(complete["t_last_s"], 60.0)
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


if __name__ == "__main__":
    unittest.main()
