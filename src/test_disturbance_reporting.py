"""核对时刻边界、断联期间阻塞，以及等概率故障统计。"""
# OpenAI Codex 0.169.0-alpha.11.1（GPT-6，OpenAI）辅助程序复查与测试。
# 工具版本发布于 2026-11-11 UTC，使用于 2026-11-21。范围与来源见 yyh/B题代码复核.md。
import json
import unittest
from pathlib import Path

from .config import VEHICLES
from .disturbance import Scenario, _base_policy
from .disturbance_reporting import FAULT_GROUP_FIELDS, SNAPSHOT_FIELDS, fault_groups, snapshots
from .timeline import _event


class SnapshotTests(unittest.TestCase):
    def test_failure_and_blocked_partner_at_recovery(self):
        path = Path(__file__).resolve().parents[1] / "results/question2/original_plan.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        scenario = Scenario("fault-outage", failed_vehicle="V4", recovery_time_s=30.)
        _, events = _base_policy(plan, scenario, "retain_original")
        rows = snapshots(plan, scenario, events)
        self.assertEqual(len(rows), 3 * len(VEHICLES))
        self.assertEqual(set(rows[0]), set(SNAPSHOT_FIELDS))
        states = {(r['vehicle_id'], r['snapshot']): r for r in rows}
        before, after, recovered = [states['V4', label] for label in
                                    ('before_disturbance', 'after_disturbance', 'recovery_before_command')]
        self.assertEqual([s['stage'] for s in (before, after, recovered)], ['SLEW', 'FAILED', 'FAILED'])
        for field in ('x_m', 'y_m', 'pan_deg', 'tilt_deg'):
            self.assertEqual(before[field], after[field])
            self.assertEqual(after[field], recovered[field])
        waiting = states['V1', 'recovery_before_command']
        self.assertEqual((waiting['task_id'], waiting['stage']), ('T2', 'WAIT_PARTNER'))

    def test_travel_interpolation_and_same_tick_start(self):
        scenario = Scenario("at-five")
        point = VEHICLES['V1']
        turn_point = (point[0] + 3, point[1])
        destination = (turn_point[0], turn_point[1] + 30)
        events = [_event('V1', 'T1', 'V2', 'departure', 'TRAVEL', 0., point,
                         path=[point, turn_point, destination])]
        for car in list(VEHICLES)[1:]:
            events.extend((_event(car, 'T2', '', 'slew_end', 'WAIT_PARTNER', 0., VEHICLES[car]),
                           _event(car, 'T2', '', 'observation_start', 'OBSERVE', 5., VEHICLES[car])))
        retained = [{**e, 'scenario_id': scenario.scenario_id, 'strategy': 'retain_original'} for e in events]
        rows = snapshots({'vehicle_events': events}, scenario, retained)
        self.assertEqual(len(rows), 2 * len(VEHICLES))
        for row in rows:
            if row['vehicle_id'] == 'V1':
                self.assertEqual((row['x_m'], row['y_m']), (turn_point[0], point[1] + 12.))
            else:
                self.assertEqual(row['stage'], 'WAIT_PARTNER')


class FaultGroupTests(unittest.TestCase):
    def test_equal_weights_ties_and_outage_conventions(self):
        rows = []
        pairs = [(1., 12.), (1., 12.), (1., 20.), (.75, 60.), (.75, 60.)]
        for interpretation in ('duration', 'recovery_time'):
            for car, (eta, time_s) in zip(VEHICLES, pairs):
                rows.append({'disturbance_set': 'failure+communication_outage', 'new_alert_id': '',
                             'new_alert_bearing_deg': '', 'new_alert_range_m': '',
                             'recovery_time_s': 10., 'outage_duration_s': 5.,
                             'outage_interpretation': interpretation, 'strategy': 'retain_original',
                             'failed_vehicle': car, 'completion_rate': eta, 't_last_s': time_s})
        self.assertEqual(fault_groups(rows[:4]), [])
        with self.assertRaises(ValueError):
            fault_groups([*rows, rows[0]])
        groups = fault_groups(rows)
        self.assertEqual(len(groups), 2)
        for row in groups:
            self.assertEqual(tuple(row), FAULT_GROUP_FIELDS)
            self.assertEqual(row['probability_per_vehicle'], .2)
            self.assertAlmostEqual(row['mean_eta'], .9)
            self.assertAlmostEqual(row['mean_t_last_s'], 32.8)
            self.assertEqual((row['best_eta'], row['best_t_last_s']), (1., 12.))
            self.assertEqual((row['worst_eta'], row['worst_t_last_s']), (.75, 60.))
            self.assertEqual(row['best_failed_vehicles'], ['V1', 'V2'])
            self.assertEqual(row['worst_failed_vehicles'], ['V4', 'V5'])


if __name__ == '__main__':
    unittest.main()
