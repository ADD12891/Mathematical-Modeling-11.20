"""Question 4: paired disturbance scenarios, replay, and reactive rescheduling."""
# OpenAI Codex 0.169.0-alpha.11.1（GPT-6，OpenAI）辅助程序复查与测试。
# 工具版本发布于 2026-11-11 UTC，使用于 2026-11-21。范围与来源见 yyh/B题代码复核.md。
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config
from .assignment import candidate_stations
from .disturbance_reporting import FAULT_GROUP_FIELDS, SNAPSHOT_FIELDS, fault_groups, snapshots
from .geometry import is_station_observable
from .localization import is_nondegenerate_pair
from .question2 import text_hash
from .routing import Router, route_length
from .timeline import slew_times, vehicle_state_at
from .timeline import _event, _pointing, _signed_turn

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "question4"
WINDOW_END_S = 60.0
DISTURBANCE_TIME_S = 5.0
NEW_ALERTS = (
    ("N1", (0.0, 30.0, 50.0)),
    ("N2", (90.0, 40.0, 50.0)),
    ("N3", (180.0, 50.0, 50.0)),
    ("N4", (270.0, 40.0, 50.0)),
)
RECOVERY_TIMES_S = (10.0, 20.0, 30.0)
OUTAGE_DURATIONS_S = (5.0, 17.5, 30.0)
SOURCE_FILES = ("disturbance.py", "disturbance_reporting.py", "config.py", "geometry.py", "routing.py", "timeline.py",
                "assignment.py", "question2.py", "localization.py")


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    new_alert: tuple[str, tuple[float, float, float]] | None = None
    failed_vehicle: str | None = None
    recovery_time_s: float | None = None
    outage_interpretation: str = "recovery_time"

    @property
    def has_outage(self):
        return self.recovery_time_s is not None

    @property
    def has_new_alert(self):
        return self.new_alert is not None

    @property
    def denominator(self):
        return len(config.ALERTS) + int(self.has_new_alert)


def build_scenarios():
    """Fixed design covering single, pairwise, and three-way disturbances."""
    scenarios = []
    for alert in NEW_ALERTS:
        scenarios.append(Scenario(f"new-{alert[0]}", new_alert=alert))
    for vehicle in config.VEHICLES:
        scenarios.append(Scenario(f"failure-{vehicle}", failed_vehicle=vehicle))
    for recovery in RECOVERY_TIMES_S:
        scenarios.append(Scenario(f"outage-rec-{recovery:g}", recovery_time_s=recovery))
    for alert, vehicle in itertools.product(NEW_ALERTS, config.VEHICLES):
        scenarios.append(Scenario(f"new-{alert[0]}-failure-{vehicle}", alert, vehicle))
    for alert, recovery in itertools.product(NEW_ALERTS, RECOVERY_TIMES_S):
        scenarios.append(Scenario(f"new-{alert[0]}-outage-rec-{recovery:g}", alert,
                                  recovery_time_s=recovery))
    for vehicle, recovery in itertools.product(config.VEHICLES, RECOVERY_TIMES_S):
        scenarios.append(Scenario(f"failure-{vehicle}-outage-rec-{recovery:g}",
                                  failed_vehicle=vehicle, recovery_time_s=recovery))
    for alert, vehicle, recovery in itertools.product(NEW_ALERTS, config.VEHICLES,
                                                       RECOVERY_TIMES_S):
        scenarios.append(Scenario(f"new-{alert[0]}-failure-{vehicle}-outage-rec-{recovery:g}",
                                  alert, vehicle, recovery))
    for duration in OUTAGE_DURATIONS_S:
        recovery = DISTURBANCE_TIME_S + duration
        scenarios.append(Scenario(f"outage-duration-{duration:g}",
                                  recovery_time_s=recovery,
                                  outage_interpretation="duration"))
    for alert, duration in itertools.product(NEW_ALERTS, OUTAGE_DURATIONS_S):
        recovery = DISTURBANCE_TIME_S + duration
        scenarios.append(Scenario(f"new-{alert[0]}-outage-duration-{duration:g}", alert,
                                  recovery_time_s=recovery,
                                  outage_interpretation="duration"))
    for vehicle, duration in itertools.product(config.VEHICLES, OUTAGE_DURATIONS_S):
        recovery = DISTURBANCE_TIME_S + duration
        scenarios.append(Scenario(f"failure-{vehicle}-outage-duration-{duration:g}",
                                  failed_vehicle=vehicle, recovery_time_s=recovery,
                                  outage_interpretation="duration"))
    for alert, vehicle, duration in itertools.product(NEW_ALERTS, config.VEHICLES,
                                                       OUTAGE_DURATIONS_S):
        recovery = DISTURBANCE_TIME_S + duration
        scenarios.append(Scenario(
            f"new-{alert[0]}-failure-{vehicle}-outage-duration-{duration:g}", alert,
            vehicle, recovery, "duration"))
    return scenarios


def _events_by_task(plan):
    events = {}
    for event in plan["vehicle_events"]:
        if event["task_id"]:
            events.setdefault((event["task_id"], event["vehicle_id"]), []).append(event)
    return events


def _task_crews(plan):
    return {task: tuple(vehicle for vehicle, tasks in plan["orders"].items() if task in tasks)
            for task in config.ALERTS}


def _event_for(events, task, vehicle, name):
    return next(event for event in events[(task, vehicle)] if event["event"] == name)


def _locked_tasks(plan, scenario):
    lock_before = scenario.recovery_time_s if scenario.has_outage else DISTURBANCE_TIME_S
    crews = _task_crews(plan)
    candidates = {task for task, start in plan["start_times"].items()
                  if start < lock_before and not (scenario.failed_vehicle in crews[task]
                                                  and start >= DISTURBANCE_TIME_S)}
    if scenario.failed_vehicle is None:
        return candidates
    started_before_failure = {task for task, start in plan["start_times"].items()
                              if start < DISTURBANCE_TIME_S}
    blocked = _failure_blocked_tasks(plan, scenario, started_before_failure)
    return candidates - blocked


def _failure_blocked_tasks(plan, scenario, locked):
    """Propagate failure only to later tasks in each affected vehicle queue."""
    if scenario.failed_vehicle is None:
        return set()
    crews = _task_crews(plan)
    blocked = {task for task, crew in crews.items()
               if scenario.failed_vehicle in crew and task not in locked}
    changed = True
    while changed:
        changed = False
        for car, sequence in plan["orders"].items():
            blocked_positions = [index for index, task in enumerate(sequence)
                                 if task in blocked]
            if not blocked_positions:
                continue
            for task in sequence[min(blocked_positions) + 1:]:
                if task not in locked and task not in blocked:
                    blocked.add(task)
                    changed = True
    return blocked


def _score(starts, denominator, task_ids):
    successful = {task: starts[task] for task in task_ids
                  if starts.get(task) is not None and 0.0 <= starts[task] <= WINDOW_END_S}
    count = len(successful)
    latest = max(successful.values(), default=0.0) if count == denominator else WINDOW_END_S
    return {"successful_alerts": count, "alert_count": denominator,
            "completion_rate": count / denominator, "t_last_s": latest,
            "task_start_times_s": {task: starts.get(task) for task in task_ids}}


def _decision_time(scenario):
    return scenario.recovery_time_s if scenario.has_outage else DISTURBANCE_TIME_S


def _first_blocked_task_by_vehicle(plan, blocked, failed_vehicle):
    first = {}
    for task in blocked:
        for car in _task_crews(plan)[task]:
            if car == failed_vehicle:
                continue
            start = plan["start_times"][task]
            if car not in first or start < first[car][0]:
                first[car] = (start, task)
    return {car: task for car, (_, task) in first.items()}


def _policy_plan_events(plan, scenario, strategy, locked, blocked, replay_remaining):
    decision_time = _decision_time(scenario)
    first_blocked = _first_blocked_task_by_vehicle(
        plan, blocked, scenario.failed_vehicle)
    events = []
    for event in plan["vehicle_events"]:
        task = event["task_id"]
        car = event["vehicle_id"]
        time_s = float(event["time_s"])
        if car == scenario.failed_vehicle and time_s >= DISTURBANCE_TIME_S:
            continue
        if (not replay_remaining and time_s >= decision_time
                and event["event"] == "next_departure"):
            continue  # Only the actual next assignment can announce a departure.
        if task in blocked:
            if car == scenario.failed_vehicle and time_s < decision_time:
                events.append({**event, "strategy": strategy,
                               "scenario_id": scenario.scenario_id})
                continue
            if (car != scenario.failed_vehicle and first_blocked.get(car) == task
                    and event["event"] not in ("observation_start", "observation_end",
                                                "reset_start", "reset_end", "next_departure")
                    and (replay_remaining or time_s < decision_time)):
                events.append({**event, "strategy": strategy,
                               "scenario_id": scenario.scenario_id})
            continue
        if replay_remaining or time_s < decision_time or task in locked:
            events.append({**event, "strategy": strategy,
                           "scenario_id": scenario.scenario_id})

    for car, task in first_blocked.items():
        event = _event_for(_events_by_task(plan), task, car, "observation_start")
        if float(event["time_s"]) >= decision_time and not replay_remaining:
            continue
        events.append({**event, "event": "blocked_wait", "stage": "WAIT_PARTNER",
                       "reason": "partner_unavailable", "strategy": strategy,
                       "scenario_id": scenario.scenario_id})
    return events


def _failure_event(plan, scenario, strategy, information_time):
    state = vehicle_state_at(plan, scenario.failed_vehicle, DISTURBANCE_TIME_S)
    return {"scenario_id": scenario.scenario_id, "strategy": strategy,
            "vehicle_id": scenario.failed_vehicle, "task_id": "", "event": "failure",
            "stage": "FAILED", "time_s": DISTURBANCE_TIME_S,
            "x_m": state["x_m"], "y_m": state["y_m"],
            "pan_deg": state["pan_deg"], "tilt_deg": state["tilt_deg"],
            "information_time_s": information_time}


def _write_csv(path, rows, fields=None):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or rows[0], lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, separators=(",", ":"),
                                                  ensure_ascii=False, allow_nan=False)
                             if isinstance(value, (dict, list, tuple)) else value
                             for key, value in row.items()})


def _write_json_lf(path, data):
    payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.write_bytes(payload.encode("utf-8"))


def _base_policy(plan, scenario, strategy):
    """Replay the frozen plan, applying failure blocking."""
    locked = _locked_tasks(plan, scenario)
    blocked = _failure_blocked_tasks(plan, scenario, locked)
    starts = {task: (plan["start_times"][task] if task not in blocked else None)
              for task in config.ALERTS}
    if scenario.has_new_alert:
        starts[scenario.new_alert[0]] = None
    events = _policy_plan_events(plan, scenario, strategy, locked, blocked,
                                 replay_remaining=True)
    if scenario.failed_vehicle:
        events.append(_failure_event(plan, scenario, strategy,
                                     scenario.recovery_time_s if scenario.has_outage
                                     else DISTURBANCE_TIME_S))
    if scenario.has_outage:
        events.append({"scenario_id": scenario.scenario_id, "strategy": strategy,
                       "vehicle_id": "ALL", "task_id": "", "event": "communication_restored",
                       "stage": "COMMUNICATION", "time_s": scenario.recovery_time_s,
                       "x_m": None, "y_m": None})
    return _score(starts, scenario.denominator, tuple(starts)), events


def _decision_pose(plan, events, car, time_s, locked, scenario):
    state = vehicle_state_at(plan, car, time_s)
    if scenario.failed_vehicle and car != scenario.failed_vehicle:
        blocked = _failure_blocked_tasks(plan, scenario, locked)
        waiting_task = min((task for task in blocked
                            if car in _task_crews(plan)[task]
                            and plan["start_times"][task] < time_s),
                           key=lambda task: plan["start_times"][task], default=None)
        if waiting_task is not None:
            arrival = _event_for(events, waiting_task, car, "arrival")
            pointing = _event_for(events, waiting_task, car, "slew_end")
            state = {**state, "stage": "WAIT_PARTNER", "task_id": waiting_task,
                     "x_m": arrival["x_m"], "y_m": arrival["y_m"],
                     "pan_deg": pointing["pan_deg"], "tilt_deg": pointing["tilt_deg"]}
    return state


def _available_after_cancel(events, state, locked, scenario, strategy):
    """Return the next east-level departure state and record any interrupted motion."""
    time_s = state["time_s"]
    car = state["vehicle_id"]
    point = (state["x_m"], state["y_m"])
    active_task = state["task_id"]
    available = {"available_s": time_s, "position": point}
    if car == scenario.failed_vehicle:
        return {**available, "available_s": math.inf}, []
    if active_task in locked:
        reset = _event_for(events, active_task, car, "reset_end")
        return {**available, "available_s": max(time_s, reset["time_s"])}, []
    if not active_task:
        return available, []
    pan, tilt = state["pan_deg"], state["tilt_deg"]
    turn = (math.degrees(_signed_turn(-math.radians(pan))), -tilt)
    reset_s = max(abs(v) for v in turn) / config.SLEW_RATE_DEG_S
    common = {"scenario_id": scenario.scenario_id, "strategy": strategy}
    history = [{**_event(car, active_task, "", "command_cancelled", "IDLE", time_s,
                         point, (pan, tilt)), **common}]
    if reset_s:
        history.extend((
            {**_event(car, active_task, "", "interrupt_reset_start", "RESET", time_s,
                      point, (pan, tilt), turn), **common},
            {**_event(car, active_task, "", "interrupt_reset_end", "IDLE",
                      time_s + reset_s, point), **common}))
    return {**available, "available_s": time_s + reset_s}, history


def _approach_events(car, task, partner, target, path, depart):
    site = path[-1]
    length = route_length(path)
    arrive = depart + length / config.SPEED
    turn_s, _ = slew_times(target, site)
    pose = tuple(map(math.degrees, _pointing(target, site)))
    ready = arrive + turn_s
    return ready, [
        _event(car, task, partner, "departure", "TRAVEL", depart, path[0],
               path=path, length=length),
        _event(car, task, partner, "arrival", "IDLE", arrive, site),
        _event(car, task, partner, "slew_start", "SLEW", arrive, site, turn=pose),
        _event(car, task, partner, "slew_end", "WAIT_PARTNER", ready, site, pose)]


def _observation_events(car, task, partner, target, site, ready, start):
    pose = tuple(map(math.degrees, _pointing(target, site)))
    reset_turn = (math.degrees(_signed_turn(-math.radians(pose[0]))), -pose[1])
    end = start + config.OBSERVATION_S
    _, reset_s = slew_times(target, site)
    events = []
    if ready < start:
        events.append(_event(car, task, partner, "wait_partner", "WAIT_PARTNER",
                             ready, site, pose))
    events.extend((
        _event(car, task, partner, "observation_start", "OBSERVE", start, site, pose),
        _event(car, task, partner, "observation_end", "RESET", end, site, pose),
        _event(car, task, partner, "reset_start", "RESET", end, site, pose, reset_turn),
        _event(car, task, partner, "reset_end", "IDLE", end + reset_s, site)))
    return end + reset_s, events


def _candidate_sites(task, alert, positions, pools):
    target = config.target_position(alert)
    if task in pools:
        candidates = pools[task]
    else:
        axis = np.arange(1.0, config.FIELD_SIZE, 2.0)
        xx, yy = np.meshgrid(axis, axis)
        points = np.column_stack((xx.ravel(), yy.ravel()))
        points = points[is_station_observable(target, points.T)]
        candidates = [tuple(map(float, point)) for point in points]
    selected = {}
    for car, position in positions.items():
        valid = list(candidates)
        valid.sort(key=lambda point: (route_length((position, point)), point))
        selected[car] = valid[:2]
        if is_station_observable(target, position):
            selected[car] = [position, *[p for p in selected[car] if p != position]][:2]
    return target, selected


def _mission_options(task, alert, state, available_cars, router, pools):
    positions = {car: state[car]["position"] for car in available_cars}
    target, sites = _candidate_sites(task, alert, positions, pools)
    options = []
    cars = sorted(available_cars)
    for first, second in itertools.combinations(cars, 2):
        for a, b in itertools.product(sites[first], sites[second]):
            if not is_nondegenerate_pair(target, a, b):
                continue
            paths = (router.route(state[first]["position"], a),
                     router.route(state[second]["position"], b))
            if any(path is None for path in paths):
                continue
            lengths = tuple(route_length(path) for path in paths)
            ready = tuple(state[car]["available_s"] + length / config.SPEED
                          + slew_times(target, site)[0]
                          for car, length, site in zip((first, second), lengths, (a, b)))
            start = max(ready)
            options.append((start, sum(lengths), first, second, a, b, paths))
    return sorted(options, key=lambda option: (option[0], option[1], option[2:6]))[:2]


def _reschedule(plan, scenario, strategy, router, pools):
    decision_time = _decision_time(scenario)
    locked = _locked_tasks(plan, scenario)
    plan_events = _events_by_task(plan)
    blocked = _failure_blocked_tasks(plan, scenario, locked)
    starts = {task: (plan["start_times"][task] if task in locked else None)
              for task in config.ALERTS}
    task_alerts = dict(config.ALERTS)
    if scenario.has_new_alert:
        task, alert = scenario.new_alert
        task_alerts[task] = alert
        starts[task] = None
    handover = _policy_plan_events(plan, scenario, strategy, locked, blocked,
                                   replay_remaining=False)
    state = {}
    for car in config.VEHICLES:
        pose = _decision_pose(plan, plan_events, car, decision_time, locked, scenario)
        state[car], interrupted = _available_after_cancel(plan_events, pose, locked,
                                                          scenario, strategy)
        handover.extend(interrupted)
    pending = tuple(task for task in task_alerts if task not in locked)
    available_cars = tuple(car for car in config.VEHICLES
                           if math.isfinite(state[car]["available_s"]))
    best = None

    def search(remaining, current, task_starts, task_events, total_distance):
        nonlocal best
        # A failed route or an exhausted station pool can leave an alert unstarted.
        score = _score(task_starts, scenario.denominator, tuple(task_alerts))
        key = (-score["successful_alerts"], score["t_last_s"], total_distance)
        if best is None or key < best[0]:
            best = (key, score, task_events)
        for task in remaining:
            for option in _mission_options(task, task_alerts[task], current, available_cars,
                                           router, pools):
                start, length, first, second, a, b, paths = option
                if start > WINDOW_END_S:
                    continue
                next_state = {car: dict(value) for car, value in current.items()}
                next_starts = dict(task_starts)
                next_events = list(task_events)
                next_starts[task] = start
                for car, site, path, partner in ((first, a, paths[0], second),
                                                 (second, b, paths[1], first)):
                    target = config.target_position(task_alerts[task])
                    depart = current[car]["available_s"]
                    ready, approach = _approach_events(car, task, partner, target,
                                                       path, depart)
                    released, observation = _observation_events(car, task, partner, target,
                                                                 site, ready, start)
                    next_state[car] = {"available_s": released, "position": site}
                    next_events.extend({**event, "scenario_id": scenario.scenario_id,
                                        "strategy": strategy} for event in approach + observation)
                search(tuple(item for item in remaining if item != task), next_state,
                       next_starts, next_events, total_distance + length)

    search(pending, state, starts, handover, 0.0)
    events = best[2]
    if scenario.failed_vehicle:
        events.append(_failure_event(plan, scenario, strategy, decision_time))
    if scenario.has_outage:
        events.append({"scenario_id": scenario.scenario_id, "strategy": strategy,
                       "vehicle_id": "ALL", "task_id": "", "event": "communication_restored",
                       "stage": "COMMUNICATION", "time_s": decision_time})
    events.append({"scenario_id": scenario.scenario_id, "strategy": strategy,
                   "vehicle_id": "CENTER", "task_id": "", "event": "reschedule_decision",
                   "stage": "DECISION", "time_s": decision_time})
    return best[1], events


def _skip_failed_task(plan, scenario, router):
    """Keep original assignments and stations while removing only failed missions."""
    decision_time = _decision_time(scenario)
    locked = _locked_tasks(plan, scenario)
    plan_events = _events_by_task(plan)
    starts = {task: (plan["start_times"][task] if task in locked else None)
              for task in config.ALERTS}
    if scenario.has_new_alert:
        starts[scenario.new_alert[0]] = None
    directly_failed = {task for task, crew in _task_crews(plan).items()
                       if scenario.failed_vehicle in crew and task not in locked}
    queues = {car: [task for task in tasks if task not in locked and task not in directly_failed]
              for car, tasks in plan["orders"].items()}
    strategy = "skip_failed_task"
    blocked = _failure_blocked_tasks(plan, scenario, locked)
    events = _policy_plan_events(plan, scenario, strategy, locked, blocked,
                                 replay_remaining=False)
    state, continuing = {}, {}
    approach_names = {"departure", "arrival", "slew_start", "slew_end"}
    for car in config.VEHICLES:
        pose = _decision_pose(plan, plan_events, car, decision_time, locked, scenario)
        task = pose["task_id"]
        if queues[car] and task == queues[car][0]:
            # This vehicle keeps its original leg, including any travel or slew
            # already performed before confirmation of the failure.
            continuing[car] = task
            state[car] = {"position": (pose["x_m"], pose["y_m"]),
                          "available_s": decision_time}
        else:
            state[car], interrupted = _available_after_cancel(plan_events, pose, locked,
                                                              scenario, strategy)
            events.extend(interrupted)
    pending = {task for queue in queues.values() for task in queue}
    crews = _task_crews(plan)
    while pending:
        # Each next task must be at the front of both original crews' queues.
        eligible = sorted(task for task in pending if all(
            queues[car] and queues[car][0] == task for car in crews[task]))
        if not eligible:
            break
        progress = False
        for task in eligible:
            target = config.target_position(config.ALERTS[task])
            legs = []
            for car in crews[task]:
                partner = next(other for other in crews[task] if other != car)
                site = plan["stations"][task][car]
                if continuing.get(car) == task:
                    ready = max(decision_time, _event_for(plan_events, task, car,
                                                          "slew_end")["time_s"])
                    approach = [e for e in plan_events[task, car]
                                if e["event"] in approach_names and e["time_s"] >= decision_time]
                else:
                    path = router.route(state[car]["position"], site)
                    if path is None:
                        break
                    ready, approach = _approach_events(car, task, partner, target, path,
                                                       state[car]["available_s"])
                legs.append((car, partner, site, ready, approach))
            if len(legs) != 2:
                continue
            start = max(leg[3] for leg in legs)
            starts[task] = start
            for car, partner, site, ready, approach in legs:
                released, observation = _observation_events(car, task, partner, target,
                                                             site, ready, start)
                events.extend({**event, "scenario_id": scenario.scenario_id, "strategy": strategy}
                              for event in approach + observation)
                state[car] = {"available_s": released, "position": site}
                continuing.pop(car, None)
                queues[car].pop(0)
            pending.remove(task)
            progress = True
        if not progress:
            break
    if scenario.failed_vehicle:
        events.append(_failure_event(plan, scenario, "skip_failed_task", decision_time))
    if scenario.has_outage:
        events.append({"scenario_id": scenario.scenario_id, "strategy": "skip_failed_task",
                       "vehicle_id": "ALL", "task_id": "", "event": "communication_restored",
                       "stage": "COMMUNICATION", "time_s": decision_time})
    return _score(starts, scenario.denominator, tuple(starts)), events


def _scenario_rows(scenario, plan, router, pools):
    strategies = [("retain_original", "retain"), ("realtime_reschedule", "reschedule")]
    if scenario.failed_vehicle:
        strategies.append(("skip_failed_task", "reschedule"))
    rows, events = [], []
    for name, mode in strategies:
        if mode == "retain":
            score, log = _base_policy(plan, scenario, name)
        elif (name == "realtime_reschedule" and scenario.has_outage
              and not scenario.has_new_alert and not scenario.failed_vehicle):
            score, log = _base_policy(plan, scenario, name)
            log.append({"scenario_id": scenario.scenario_id, "strategy": name,
                        "vehicle_id": "CENTER", "task_id": "",
                        "event": "reschedule_decision", "stage": "DECISION",
                        "time_s": scenario.recovery_time_s,
                        "decision": "original plan retained; no pending disturbance task"})
        elif name == "skip_failed_task":
            score, log = _skip_failed_task(plan, scenario, router)
        else:
            score, log = _reschedule(plan, scenario, name, router, pools)
        rows.append({"scenario_id": scenario.scenario_id,
                     "disturbance_set": _disturbance_label(scenario),
                     "new_alert_id": scenario.new_alert[0] if scenario.new_alert else "",
                     "new_alert_bearing_deg": scenario.new_alert[1][0] if scenario.new_alert else "",
                     "new_alert_range_m": scenario.new_alert[1][1] if scenario.new_alert else "",
                     "failed_vehicle": scenario.failed_vehicle or "",
                     "failure_time_s": 5.0 if scenario.failed_vehicle else "",
                     "failure_known_time_s": (scenario.recovery_time_s if scenario.has_outage
                                              and scenario.failed_vehicle else
                                              5.0 if scenario.failed_vehicle else ""),
                     "recovery_time_s": scenario.recovery_time_s or "",
                     "outage_duration_s": (scenario.recovery_time_s - 5.0
                                            if scenario.has_outage else ""),
                     "outage_interpretation": (scenario.outage_interpretation
                                               if scenario.has_outage else ""),
                     "strategy": name, **score})
        events.extend(log)
    return rows, events


def _disturbance_label(scenario):
    labels = []
    if scenario.has_new_alert:
        labels.append("new_alert")
    if scenario.failed_vehicle:
        labels.append("failure")
    if scenario.has_outage:
        labels.append("communication_outage")
    return "+".join(labels)


def _experiment_interpretation(scenario):
    return scenario.outage_interpretation if scenario.has_outage else "not_applicable"


def _source_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _new_alert_station_pools():
    axis = np.arange(1.0, config.FIELD_SIZE, 2.0)
    xx, yy = np.meshgrid(axis, axis)
    points = np.column_stack((xx.ravel(), yy.ravel()))
    pools = {}
    for task, alert in NEW_ALERTS:
        target = config.target_position(alert)
        visible = is_station_observable(target, points.T)
        pools[task] = [tuple(map(float, point)) for point in points[visible]]
    return pools


def _revision():
    paths = ["src/" + name for name in SOURCE_FILES]
    try:
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--", *paths],
                                       cwd=ROOT, text=True, stderr=subprocess.DEVNULL)
        if dirty:
            return None
        return subprocess.check_output(["git", "log", "-1", "--format=%H", "--", *paths],
                                       cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _aggregate(scores):
    grouped = {}
    for row in scores:
        key = row["disturbance_set"], row["outage_interpretation"], row["strategy"]
        grouped.setdefault(key, []).append(row)
    rows = []
    for (disturbance, interpretation, strategy), group in sorted(grouped.items()):
        n = len(group)
        # 两个极值各来自同一条场景记录，先比较完成率，再比较最晚启动时刻。
        ranked = sorted(group, key=lambda r: (-r["completion_rate"], r["t_last_s"]))
        best, worst = ranked[0], ranked[-1]
        rows.append({"disturbance_set": disturbance, "strategy": strategy,
                     "outage_interpretation": interpretation,
                     "scenario_count": n,
                     "mean_completion_rate": sum(float(x["completion_rate"]) for x in group) / n,
                     "mean_t_last_s": sum(float(x["t_last_s"]) for x in group) / n,
                     "best_completion_rate": best["completion_rate"],
                     "best_t_last_s": best["t_last_s"], "best_scenario_id": best["scenario_id"],
                     "worst_completion_rate": worst["completion_rate"],
                     "worst_t_last_s": worst["t_last_s"], "worst_scenario_id": worst["scenario_id"],
                     "mean_successful_alerts": sum(int(x["successful_alerts"]) for x in group) / n})
    return rows


def _load_frozen_plan(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    name = manifest["original_plan"]
    plan_path = directory / name
    if text_hash(plan_path) != manifest["output_sha256"][name]:
        raise ValueError("第二问原计划与其冻结清单不一致，请核对 original_plan.json 与 manifest.json 的版本")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not plan.get("feasible"):
        raise ValueError("第二问冻结原计划不可行")
    return plan, manifest["output_sha256"][name]


def _output_directory(output_dir, scenario_id):
    if scenario_id and output_dir.resolve() == OUT.resolve():
        return OUT / "debug" / scenario_id
    return output_dir


def run_question4(output_dir=OUT, scenario_id=None, *, plan_dir=ROOT / "results" / "question2"):
    scenarios = build_scenarios()
    if scenario_id is not None:
        scenarios = [scenario for scenario in scenarios if scenario.scenario_id == scenario_id]
        if not scenarios:
            raise ValueError(f"未知场景 {scenario_id}，请用 --list 查看场景编号")
    output_dir = _output_directory(output_dir, scenario_id)
    plan, plan_hash = _load_frozen_plan(Path(plan_dir))
    router = Router()
    pools = candidate_stations()
    pools.update(_new_alert_station_pools())
    scores, all_events, states = [], [], []
    for scenario in scenarios:
        rows, events = _scenario_rows(scenario, plan, router, pools)
        scores.extend(rows)
        # Stable sort retains the within-vehicle order of zero-duration stages.
        all_events.extend(sorted(events, key=lambda e: (e["strategy"], e["vehicle_id"], e["time_s"])))
        states.extend(snapshots(plan, scenario, events, disturbance_time_s=DISTURBANCE_TIME_S))
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "scenario_scores.csv", scores)
    fields = sorted({key for event in all_events for key in event})
    _write_csv(output_dir / "event_log.csv", all_events, fields)
    aggregate = _aggregate(scores)
    _write_csv(output_dir / "aggregate_scores.csv", aggregate)
    _write_csv(output_dir / "snapshots.csv", states, SNAPSHOT_FIELDS)
    _write_csv(output_dir / "fault_groups.csv", fault_groups(scores), FAULT_GROUP_FIELDS)
    scenario_rows = [
        {"scenario_id": scenario.scenario_id, "disturbance_set": _disturbance_label(scenario),
         "new_alert": scenario.new_alert, "failed_vehicle": scenario.failed_vehicle,
         "failure_time_s": 5.0 if scenario.failed_vehicle else None,
         "recovery_time_s": scenario.recovery_time_s,
         "outage_duration_s": scenario.recovery_time_s - 5.0
         if scenario.has_outage else None,
         "outage_interpretation": _experiment_interpretation(scenario),
         "denominator": scenario.denominator}
        for scenario in scenarios]
    _write_json_lf(output_dir / "scenarios.json", scenario_rows)
    outputs = ("scenario_scores.csv", "event_log.csv", "aggregate_scores.csv", "scenarios.json",
               "snapshots.csv", "fault_groups.csv")
    manifest = {
        "source_revision": _revision(),
        "source_sha256": {name: text_hash(ROOT / "src" / name) for name in SOURCE_FILES},
        "source_plan_sha256": plan_hash,
        "output_sha256": {name: _source_hash(output_dir / name) for name in outputs},
        "hash_convention": "Sources and Q2 plan: UTF-8 text normalized to LF. Q4 outputs: exact file bytes.",
        "scenario_count": len(scenarios), "strategy_rows": len(scores),
        "policy_notes": {
            "initial_plan_fully_dispatched_at_t0": True,
            "same_time_rule": "advance to t=5; lock observations with start < t=5; apply disturbances; reconsider starts at t=5",
            "outage_main_interpretation": "recovery time t_rec in (5,30], duration t_rec-5",
            "outage_sensitivity_interpretation": "duration d in [5,30] s, recovery t_rec=5+d; reported separately",
            "new_alert_sampling": "four fixed cardinal bearings with ranges spanning 30 to 50 m, H=50 m",
            "rescheduler_scope": "enumerate task orders; try up to two nearby sites per vehicle and retain up to two joint options per task at each search node",
            "score": "maximize successful starts within [0,60], then minimize t_last; missing task sets t_last=60",
        },
        "strategies": ["retain_original", "realtime_reschedule", "skip_failed_task"],
        "summary": aggregate,
    }
    _write_json_lf(output_dir / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--plan-dir", type=Path, default=ROOT / "results" / "question2",
                        help="第二问生成的目录，须含原计划及对应冻结清单")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--scenario", metavar="ID", help="仅运行指定场景")
    modes.add_argument("--list", action="store_true", help="列出场景编号并退出")
    args = parser.parse_args()
    if args.list:
        print("\n".join(s.scenario_id for s in build_scenarios()))
        return
    try:
        result = run_question4(args.output_dir, args.scenario, plan_dir=args.plan_dir)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Question 4: {result['scenario_count']} scenarios, "
          f"{result['strategy_rows']} strategy rows")
    print(f"Results: {_output_directory(args.output_dir, args.scenario).resolve()}")


if __name__ == "__main__":
    main()
