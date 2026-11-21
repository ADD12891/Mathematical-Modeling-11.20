"""Question 4: paired disturbance scenarios, replay, and reactive rescheduling."""
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
from .geometry import is_station_observable
from .localization import is_nondegenerate_pair
from .routing import Router, route_length
from .timeline import slew_times, vehicle_state_at

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
SOURCE_FILES = ("disturbance.py", "config.py", "geometry.py", "routing.py", "timeline.py",
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
    return {task for task, start in plan["start_times"].items()
            if start < lock_before and not (scenario.failed_vehicle in crews[task]
                                            and start >= DISTURBANCE_TIME_S)}


def _failure_blocked_tasks(plan, scenario, locked):
    """Propagate failure to tasks queued behind the healthy partner waiting on it."""
    if scenario.failed_vehicle is None:
        return set()
    crews = _task_crews(plan)
    blocked = {task for task, crew in crews.items()
               if scenario.failed_vehicle in crew and task not in locked}
    changed = True
    while changed:
        changed = False
        unavailable = {scenario.failed_vehicle}
        for task in blocked:
            unavailable.update(car for car in crews[task] if car != scenario.failed_vehicle)
        for task, crew in crews.items():
            if task not in locked and task not in blocked and unavailable.intersection(crew):
                blocked.add(task)
                changed = True
    return blocked


def _score(starts, denominator, task_ids):
    successful = {task: start for task, start in starts.items()
                  if start is not None and 0.0 <= start <= WINDOW_END_S}
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
                       "reason": "failed_partner", "strategy": strategy,
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


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0], lineterminator="\n")
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


def _current_vehicle_state(plan, events, car, time_s, locked, scenario):
    state = vehicle_state_at(plan, car, time_s)
    if state["stage"] == "FAILED":
        return {"available_s": math.inf, "position": (state["x_m"], state["y_m"]),
                "pan_deg": state["pan_deg"], "tilt_deg": state["tilt_deg"]}
    active_task = state["task_id"]
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
            active_task = waiting_task
    if active_task in locked:
        reset = _event_for(events, active_task, car, "reset_end")
        arrival = _event_for(events, active_task, car, "arrival")
        return {"available_s": max(time_s, reset["time_s"]),
                "position": (arrival["x_m"], arrival["y_m"]), "pan_deg": 0.0,
                "tilt_deg": 0.0}
    pan, tilt = state["pan_deg"], state["tilt_deg"]
    reset_s = max(abs(pan), abs(tilt)) / config.SLEW_RATE_DEG_S
    return {"available_s": time_s + reset_s, "position": (state["x_m"], state["y_m"]),
            "pan_deg": 0.0, "tilt_deg": 0.0}


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
            reset = tuple(slew_times(target, site)[1] for site in (a, b))
            options.append((start, sum(lengths), first, second, a, b, paths, reset))
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
    state = {car: _current_vehicle_state(plan, plan_events, car, decision_time, locked, scenario)
             for car in config.VEHICLES}
    if scenario.failed_vehicle:
        state[scenario.failed_vehicle]["available_s"] = math.inf
    pending = tuple(task for task in task_alerts if task not in locked)
    available_cars = tuple(car for car in config.VEHICLES
                           if math.isfinite(state[car]["available_s"]))
    best = None

    def search(remaining, current, task_starts, task_events, total_distance):
        nonlocal best
        if not remaining:
            score = _score(task_starts, scenario.denominator, tuple(task_alerts))
            key = (-score["successful_alerts"], score["t_last_s"], total_distance)
            if best is None or key < best[0]:
                best = (key, score, task_events)
            return
        for task in remaining:
            for option in _mission_options(task, task_alerts[task], current, available_cars,
                                           router, pools):
                start, length, first, second, a, b, paths, reset = option
                end = start + config.OBSERVATION_S
                next_state = {car: dict(value) for car, value in current.items()}
                next_starts = dict(task_starts)
                next_events = list(task_events)
                next_starts[task] = start
                for car, site, path, reset_s, partner in (
                        (first, a, paths[0], reset[0], second),
                        (second, b, paths[1], reset[1], first)):
                    target = config.target_position(task_alerts[task])
                    slew, _ = slew_times(target, site)
                    depart = current[car]["available_s"]
                    arrive = depart + route_length(path) / config.SPEED
                    ready = arrive + slew
                    leg = route_length(path)
                    next_state[car] = {"available_s": end + reset_s, "position": site,
                                       "pan_deg": 0.0, "tilt_deg": 0.0}
                    event_specs = (
                        ("departure", "TRAVEL", depart, current[car]["position"]),
                        ("arrival", "IDLE", arrive, site),
                        ("slew_start", "SLEW", arrive, site),
                        ("slew_end", "WAIT_PARTNER", ready, site),
                        ("observation_start", "OBSERVE", start, site),
                        ("observation_end", "RESET", end, site),
                        ("reset_start", "RESET", end, site),
                        ("reset_end", "IDLE", end + reset_s, site))
                    for event_name, stage, time_s, position in event_specs:
                        next_events.append({
                            "scenario_id": scenario.scenario_id, "strategy": strategy,
                            "vehicle_id": car, "partner_id": partner, "task_id": task,
                            "event": event_name, "stage": stage, "time_s": time_s,
                            "x_m": position[0], "y_m": position[1], "route_xy_m": path,
                            "leg_distance_m": leg})
                search(tuple(item for item in remaining if item != task), next_state,
                       next_starts, next_events, total_distance + length)

    locked_events = _policy_plan_events(plan, scenario, strategy, locked, blocked,
                                         replay_remaining=False)
    search(pending, state, starts, locked_events, 0.0)
    if best is None:
        raise RuntimeError(f"重调度未生成候选结果：{scenario.scenario_id}")
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
    state = {car: _current_vehicle_state(plan, plan_events, car, decision_time, locked, scenario)
             for car in config.VEHICLES}
    if scenario.failed_vehicle:
        state[scenario.failed_vehicle]["available_s"] = math.inf
    blocked = _failure_blocked_tasks(plan, scenario, locked)
    events = _policy_plan_events(plan, scenario, "skip_failed_task", locked, blocked,
                                 replay_remaining=False)
    pending = {task for queue in queues.values() for task in queue}
    while pending:
        eligible = [task for task in pending
                    if all(queues[car] and queues[car][0] == task
                           for car in _task_crews(plan)[task])]
        if not eligible:
            break
        choices = []
        for task in eligible:
            crew = _task_crews(plan)[task]
            target = config.target_position(config.ALERTS[task])
            sites = plan["stations"][task]
            option = []
            for car in crew:
                path = router.route(state[car]["position"], sites[car])
                if path is None or not math.isfinite(state[car]["available_s"]):
                    option = []
                    break
                turn, reset = slew_times(target, sites[car])
                arrival = state[car]["available_s"] + route_length(path) / config.SPEED
                option.append((car, path, arrival, turn, reset))
            if len(option) != 2:
                continue
            start = max(item[2] + item[3] for item in option)
            choices.append((start, task, option))
        if not choices:
            break
        start, task, option = min(choices, key=lambda item: (item[0], item[1]))
        end = start + config.OBSERVATION_S
        starts[task] = start
        for car, path, arrival, turn, reset in option:
            site = plan["stations"][task][car]
            partner = next(other for other in _task_crews(plan)[task] if other != car)
            leg = route_length(path)
            reference = _event_for(plan_events, task, car, "slew_end")
            reset_reference = _event_for(plan_events, task, car, "reset_start")
            common = {"scenario_id": scenario.scenario_id,
                      "strategy": "skip_failed_task", "vehicle_id": car,
                      "partner_id": partner, "task_id": task,
                      "x_m": site[0], "y_m": site[1]}
            events.extend((
                {**common, "event": "departure", "stage": "TRAVEL",
                 "time_s": state[car]["available_s"],
                 "x_m": state[car]["position"][0],
                 "y_m": state[car]["position"][1], "route_xy_m": path,
                 "leg_distance_m": leg, "pan_deg": state[car]["pan_deg"],
                 "tilt_deg": state[car]["tilt_deg"]},
                {**common, "event": "arrival", "stage": "IDLE", "time_s": arrival,
                 "pan_deg": state[car]["pan_deg"], "tilt_deg": state[car]["tilt_deg"]},
                {**common, "event": "slew_start", "stage": "SLEW", "time_s": arrival,
                 "pan_deg": state[car]["pan_deg"], "tilt_deg": state[car]["tilt_deg"],
                 "signed_pan_delta_deg": reference["signed_pan_delta_deg"],
                 "tilt_delta_deg": reference["tilt_delta_deg"]},
                {**common, "event": "slew_end", "stage": "WAIT_PARTNER",
                 "time_s": arrival + turn, "pan_deg": reference["pan_deg"],
                 "tilt_deg": reference["tilt_deg"]},
                *([{"scenario_id": scenario.scenario_id,
                    "strategy": "skip_failed_task", "vehicle_id": car,
                    "partner_id": partner, "task_id": task, "event": "wait_partner",
                    "stage": "WAIT_PARTNER", "time_s": arrival + turn,
                    "x_m": site[0], "y_m": site[1], "pan_deg": reference["pan_deg"],
                    "tilt_deg": reference["tilt_deg"]}]
                  if arrival + turn < start else []),
                {**common, "event": "observation_start", "stage": "OBSERVE",
                 "time_s": start, "pan_deg": reference["pan_deg"],
                 "tilt_deg": reference["tilt_deg"]},
                {**common, "event": "observation_end", "stage": "RESET", "time_s": end,
                 "pan_deg": reference["pan_deg"], "tilt_deg": reference["tilt_deg"]},
                {**common, "event": "reset_start", "stage": "RESET", "time_s": end,
                 "pan_deg": reference["pan_deg"], "tilt_deg": reference["tilt_deg"],
                 "signed_pan_delta_deg": reset_reference["signed_pan_delta_deg"],
                 "tilt_delta_deg": reset_reference["tilt_delta_deg"]},
                {**common, "event": "reset_end", "stage": "IDLE", "time_s": end + reset,
                 "pan_deg": 0.0, "tilt_deg": 0.0}))
            state[car] = {"available_s": end + reset, "position": site,
                          "pan_deg": 0.0, "tilt_deg": 0.0}
            queues[car].pop(0)
        pending.remove(task)
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
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                       text=True).strip()
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
        rows.append({"disturbance_set": disturbance, "strategy": strategy,
                     "outage_interpretation": interpretation,
                     "scenario_count": n,
                     "mean_completion_rate": sum(float(x["completion_rate"]) for x in group) / n,
                     "mean_t_last_s": sum(float(x["t_last_s"]) for x in group) / n,
                     "best_completion_rate": max(float(x["completion_rate"]) for x in group),
                     "worst_completion_rate": min(float(x["completion_rate"]) for x in group),
                     "mean_successful_alerts": sum(int(x["successful_alerts"]) for x in group) / n})
    return rows


def run_question4(output_dir=OUT):
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = ROOT / "results" / "question2" / "original_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not plan.get("feasible"):
        raise ValueError("第二问冻结原计划不可行")
    router = Router()
    pools = candidate_stations()
    pools.update(_new_alert_station_pools())
    scenarios = build_scenarios()
    scores, all_events = [], []
    for scenario in scenarios:
        rows, events = _scenario_rows(scenario, plan, router, pools)
        scores.extend(rows)
        all_events.extend(events)
    _write_csv(output_dir / "scenario_scores.csv", scores)
    fields = sorted({key for event in all_events for key in event})
    with (output_dir / "event_log.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(all_events)
    aggregate = _aggregate(scores)
    _write_csv(output_dir / "aggregate_scores.csv", aggregate)
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
    outputs = ("scenario_scores.csv", "event_log.csv", "aggregate_scores.csv", "scenarios.json")
    manifest = {
        "source_revision": _revision(),
        "source_sha256": {name: _source_hash(ROOT / "src" / name) for name in SOURCE_FILES},
        "source_plan_sha256": _source_hash(plan_path),
        "output_sha256": {name: _source_hash(output_dir / name) for name in outputs},
        "hash_convention": "SHA-256 of file bytes.",
        "scenario_count": len(scenarios), "strategy_rows": len(scores),
        "policy_notes": {
            "initial_plan_fully_dispatched_at_t0": True,
            "same_time_rule": "advance to t=5; lock observations with start < t=5; apply disturbances; reconsider starts at t=5",
            "outage_main_interpretation": "recovery time t_rec in (5,30], duration t_rec-5",
            "outage_sensitivity_interpretation": "duration d in [5,30] s, recovery t_rec=5+d; reported separately",
            "new_alert_sampling": "four fixed cardinal bearings with ranges spanning 30 to 50 m, H=50 m",
            "rescheduler_scope": "task-order enumeration with nearest-two feasible stations per vehicle; heuristic optimum is limited to this finite pool",
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
    args = parser.parse_args()
    result = run_question4(args.output_dir)
    print(f"Question 4: {result['scenario_count']} scenarios, "
          f"{result['strategy_rows']} strategy rows")
    print(f"Results: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
