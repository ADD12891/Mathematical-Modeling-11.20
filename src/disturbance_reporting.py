"""第四问的执行状态快照和给定场景条件下的五车故障统计。"""
# OpenAI Codex（GPT-6，OpenAI）于 2026-11-21 辅助编写快照、汇总与测试。
# 模型发布日期未在本次运行环境中列出。使用范围见 yyh/B题代码复核.md。
from math import fsum

from .config import VEHICLES
from .timeline import vehicle_state_at


SNAPSHOT_FIELDS = ("scenario_id", "snapshot", "vehicle_id", "task_id", "time_s", "stage",
                   "x_m", "y_m", "pan_deg", "tilt_deg")
_GROUP_KEYS = ("disturbance_set", "new_alert_id", "new_alert_bearing_deg", "new_alert_range_m",
               "recovery_time_s", "outage_duration_s", "outage_interpretation", "strategy")
FAULT_GROUP_FIELDS = (*_GROUP_KEYS, "vehicle_count", "probability_per_vehicle", "mean_eta",
                      "mean_t_last_s", "best_eta", "best_t_last_s", "best_failed_vehicles",
                      "worst_eta", "worst_t_last_s", "worst_failed_vehicles")


def snapshots(plan, scenario, retained_events, *, disturbance_time_s=5.0):
    """结算到指定时刻的行驶和转动。扰动和恢复的同刻指令均在快照之后执行。"""
    retained = [e for e in retained_events if e["vehicle_id"] in VEHICLES
                and e["scenario_id"] == scenario.scenario_id and e["strategy"] == "retain_original"]
    # 5 s 前各策略执行相同计划。物理故障先于同刻启动生效；断联期间保留策略的
    # 执行日志已排除遭阻塞的未来事件，也保留了故障车退出时的真实位置和姿态。
    checks = [("before_disturbance", disturbance_time_s, plan["vehicle_events"]),
              ("after_disturbance", disturbance_time_s, retained)]
    if scenario.has_outage:
        checks.append(("recovery_before_command", scenario.recovery_time_s, retained))

    rows = []
    for label, time_s, events in checks:
        prior = [e for e in events if e["time_s"] < time_s or
                 (label == "after_disturbance" and e["time_s"] == time_s and e["event"] == "failure")]
        # 相同时刻同车事件保留原执行顺序，例如零行程的出发、到站、开始转向。
        replay = {"feasible": True, "vehicle_events": sorted(prior, key=lambda e: e["time_s"])}
        for car in VEHICLES:
            state = vehicle_state_at(replay, car, time_s)
            rows.append({"scenario_id": scenario.scenario_id, "snapshot": label, **state})
    return rows


def fault_groups(score_rows):
    """固定新增告警、恢复时刻和策略，仅对故障车身份按题给的等概率求期望。"""
    groups = {}
    for row in score_rows:
        car = row["failed_vehicle"]
        if not car:
            continue
        key = tuple(row[field] for field in _GROUP_KEYS)
        scores = groups.setdefault(key, {})
        if car not in VEHICLES or car in scores:
            raise ValueError(f"故障汇总分组中的车辆编号无效或重复：{key}, {car}")
        scores[car] = (float(row["completion_rate"]), float(row["t_last_s"]))

    rows = []
    for key, scores in groups.items():
        if scores.keys() != VEHICLES.keys():
            continue  # 单场景调试不能代表五车等概率故障。
        ordered = sorted(scores.values(), key=lambda pair: (-pair[0], pair[1]))
        best, worst = ordered[0], ordered[-1]
        count = len(scores)
        rows.append({**dict(zip(_GROUP_KEYS, key)), "vehicle_count": count,
                     "probability_per_vehicle": 1 / count,
                     "mean_eta": fsum(pair[0] for pair in ordered) / count,
                     "mean_t_last_s": fsum(pair[1] for pair in ordered) / count,
                     "best_eta": best[0], "best_t_last_s": best[1],
                     "best_failed_vehicles": [car for car in VEHICLES if scores[car] == best],
                     "worst_eta": worst[0], "worst_t_last_s": worst[1],
                     "worst_failed_vehicles": [car for car in VEHICLES if scores[car] == worst]})
    return rows
