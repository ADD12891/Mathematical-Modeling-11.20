"""联算接口、里程口径与同场景配对的回归测试。"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from .disturbance import run_question4
from .question2 import text_hash
from .study import ROOT, accuracy_changes, compare_scenarios, observation_rows, write_study


class StudyTests(unittest.TestCase):
    def test_supplied_plan_is_used_even_when_a_default_plan_exists(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            plan = folder / "original_plan.json"
            plan.write_text('{"feasible":false}', encoding="utf-8")
            (folder / "manifest.json").write_text(json.dumps({
                "original_plan": plan.name, "output_sha256": {plan.name: text_hash(plan)}
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "不可行"):
                run_question4(folder / "out", "new-N1", plan_dir=folder)

    def test_study_rejects_mismatched_replay_before_reporting(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("question2", "question4"):
                (root / name).mkdir()
            (root / "question2/original_plan.json").write_text("{}", encoding="utf-8")
            (root / "question4/manifest.json").write_text('{"source_plan_sha256":"bad"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "本轮第二问"):
                write_study(root)

    def test_reused_vehicle_carries_mileage_across_stationary_leg(self):
        rows = observation_rows(ROOT / "results")
        self.assertEqual(len(rows), 14)
        reused = [r for r in rows if (r["strategy"], r["vehicle"]) == ("optimized", "V3")]
        self.assertEqual({r["task"] for r in reused}, {"T1", "T3"})
        self.assertGreater(reused[0]["cumulative_distance_m"], 3)
        self.assertEqual(reused[0]["cumulative_distance_m"], reused[1]["cumulative_distance_m"])
        self.assertNotEqual(reused[0]["horizontal_range_m"], reused[1]["horizontal_range_m"])
        self.assertEqual(reused[0]["q3_reference_range_m"], 40)
        self.assertEqual(reused[0]["q3_actual_probability"], reused[1]["q3_actual_probability"])
        self.assertAlmostEqual(next(r["ratio_to_best_T2_rms"] for r in rows if r["strategy"] == "precision"), 1.)
        self.assertTrue(all(0 < r["fraction_of_q1_best_T1_quality"] <= 1 for r in rows if r["task"] == "T1"))

    def test_more_starts_at_window_end_has_priority_over_equal_times(self):
        base = {"scenario_id": "example", "strategy": "retain_original", "alert_count": "4",
                "successful_alerts": "3", "t_last_s": "60", "outage_interpretation": "duration"}
        dynamic = dict(base, strategy="realtime_reschedule", successful_alerts="4")
        row, = compare_scenarios([dynamic, base])
        self.assertEqual((row["outcome"], row["group"]), ("more_starts", "duration"))
        with self.assertRaisesRegex(ValueError, "重复"):
            compare_scenarios([base, dynamic, dynamic])

    def test_range_and_angle_have_opposing_effects_for_T2(self):
        rows = observation_rows(ROOT / "results")
        t2 = next(r for r in accuracy_changes(rows) if r["task"] == "T2")
        self.assertGreater(t2["range_factor"], 1)
        self.assertLess(t2["angle_factor"], 1)
        self.assertAlmostEqual(t2["range_factor"] * t2["angle_factor"], 1.682799506 / 3.747165059)
        for row in rows:
            if (row["task"], row["strategy"]) == ("T2", "optimized"):
                row["pair_rms_m"] *= 2
        with self.assertRaisesRegex(ValueError, "T2"):
            accuracy_changes(rows)
