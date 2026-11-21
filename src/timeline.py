"""问题二（2）：对固定分车、站点和任务顺序生成最早可行的无扰动时间线。"""
from graphlib import CycleError, TopologicalSorter
from math import atan2, copysign, degrees, dist, hypot, isfinite, pi, radians

from .config import (
    ALERTS, BODY_TOL_M, CAMERA_HEIGHT, FIELD_SIZE, OBSERVATION_S, SLEW_RATE_DEG_S,
    SPEED, VEHICLES, VEHICLE_RADIUS, target_position,
)
from .geometry import is_station_observable
from .localization import is_nondegenerate_pair
from .routing import route_length, segment_clearance

LEG_TOL_M = 1e-9


def _signed_turn(angle):
    """弧度，最短水平转角；恰好半圈取负。"""
    return (angle + pi) % (2 * pi) - pi


def _pointing(target, station):
    dx, dy = target[0] - station[0], target[1] - station[1]
    return _signed_turn(atan2(dy, dx)), atan2(target[2] - CAMERA_HEIGHT, hypot(dx, dy))


def slew_times(target, station):
    """返回从正东水平指向目标、观测后复位的时间，两轴同步转动，单位 s。"""
    pan, tilt = _pointing(target, station)
    speed = radians(SLEW_RATE_DEG_S)
    return max(abs(pan), abs(tilt)) / speed, max(abs(_signed_turn(-pan)), abs(tilt)) / speed


class InvalidSchedule(ValueError):
    """候选排程有不可执行的动作或缺少满足约束的路线。"""


def _task_order(orders, stations):
    if set(stations) != set(ALERTS) or set(orders) - set(VEHICLES):
        raise InvalidSchedule("任务或车辆编号与题给输入不一致")
    crews = {task: set() for task in ALERTS}
    graph = TopologicalSorter()
    for task in ALERTS:
        graph.add(task)
    for vehicle, sequence in orders.items():
        if len(set(sequence)) != len(sequence):
            raise InvalidSchedule(f"{vehicle} 重复领取同一任务")
        for task in sequence:
            if task not in ALERTS:
                raise InvalidSchedule(f"{vehicle} 包含未知任务 {task}")
            crews[task].add(vehicle)
        for before, after in zip(sequence, sequence[1:]):
            graph.add(after, before)
    for task, crew in crews.items():
        if len(crew) != 2 or set(stations[task]) != crew:
            raise InvalidSchedule(f"{task} 要求两辆不同车辆且各自有对应站点")
    try:
        return list(graph.static_order())
    except CycleError as exc:
        raise InvalidSchedule(f"车辆任务先后关系成环：{' -> '.join(exc.args[1])}") from exc


def _checked_path(router, origin, station, max_leg_m):
    if max_leg_m is not None and dist(origin, station) > max_leg_m + LEG_TOL_M:
        raise InvalidSchedule("直线距离下界已超过单段行驶限制")
    path = router.route(origin, station)
    if not path:
        raise InvalidSchedule("未找到可通行路线，需加密搜索")
    if tuple(path[0]) != tuple(origin) or tuple(path[-1]) != tuple(station):
        raise InvalidSchedule("路线端点与出发、到站位置不一致")
    for point in path:
        if not all(VEHICLE_RADIUS <= c <= FIELD_SIZE - VEHICLE_RADIUS for c in point):
            raise InvalidSchedule("路线越过允许的车心场界")
    if any(segment_clearance(a, b) < -BODY_TOL_M for a, b in zip(path, path[1:])):
        raise InvalidSchedule("路线中段侵入建筑")
    length = route_length(path)
    if max_leg_m is not None and length > max_leg_m + LEG_TOL_M:
        raise InvalidSchedule("当前绕行路线超过单段限制，直线下界未超，需加密搜索")
    return path, length


def _event(vehicle, task, partner, event, stage, time, point, pose=(0.0, 0.0),
           turn=(0.0, 0.0), path=None, length=0.0):
    return {
        "vehicle_id": vehicle, "task_id": task, "partner_id": partner,
        "event": event, "stage": stage, "time_s": time, "x_m": point[0], "y_m": point[1],
        "pan_deg": pose[0], "tilt_deg": pose[1],
        "signed_pan_delta_deg": turn[0], "tilt_delta_deg": turn[1],
        "route_xy_m": path, "leg_distance_m": length,
    }


def _evaluate(orders, stations, router, max_leg_m):
    if max_leg_m is not None and (not isfinite(max_leg_m) or max_leg_m < 0):
        raise InvalidSchedule("行驶距离限制必须是非负有限数")
    sequence = _task_order(orders, stations)
    positions = dict(VEHICLES)
    available = dict.fromkeys(VEHICLES, 0.0)
    starts, finishes = {}, {}
    events = [_event(v, "", "", "idle", "IDLE", 0.0, p) for v, p in VEHICLES.items()
              if not orders.get(v)]
    total_distance = total_wait = 0.0

    for task in sequence:
        target = target_position(ALERTS[task])
        assignment = stations[task]
        crew = sorted(assignment)
        points = [tuple(assignment[v]) for v in crew]
        for v, point in zip(crew, points):
            if (len(point) != 2 or not all(map(isfinite, point))
                    or not is_station_observable(target, point)):
                raise InvalidSchedule(f"{task} 的 {v} 站点不可观测：{point}")
        if not is_nondegenerate_pair(target, *points):
            raise InvalidSchedule(f"{task} 站对不能给出数值可分辨的唯一交点")
        ready = {}
        poses = {}
        reset_durations = {}
        for v, partner, point in zip(crew, reversed(crew), points):
            origin, departure = positions[v], available[v]
            try:
                path, length = _checked_path(router, origin, point, max_leg_m)
            except InvalidSchedule as exc:
                raise InvalidSchedule(f"{v} 前往 {task}：{exc}") from exc
            arrival = departure + length / SPEED
            turn_s, reset_durations[v] = slew_times(target, point)
            ready[v] = arrival + turn_s
            pose = tuple(map(degrees, _pointing(target, point)))
            poses[v] = pose
            events.extend((
                _event(v, task, partner, "departure", "TRAVEL", departure, origin,
                       path=path, length=length),
                _event(v, task, partner, "arrival", "IDLE", arrival, point),
                _event(v, task, partner, "slew_start", "SLEW", arrival, point, turn=pose),
                _event(v, task, partner, "slew_end", "WAIT_PARTNER", ready[v], point, pose),
            ))
            total_distance += length
            positions[v] = point

        starts[task] = start = max(ready.values())
        finishes[task] = end = start + OBSERVATION_S
        for v, partner in zip(crew, reversed(crew)):
            point, pose = positions[v], poses[v]
            if ready[v] < start:
                events.append(_event(v, task, partner, "wait_partner", "WAIT_PARTNER",
                                     ready[v], point, pose))
            total_wait += start - ready[v]
            available[v] = end + reset_durations[v]
            reset_turn = degrees(_signed_turn(-radians(pose[0]))), -pose[1]
            events.extend((
                _event(v, task, partner, "observation_start", "OBSERVE", start, point, pose),
                _event(v, task, partner, "observation_end", "RESET", end, point, pose),
                _event(v, task, partner, "reset_start", "RESET", end, point, pose, reset_turn),
                _event(v, task, partner, "reset_end", "IDLE", available[v], point),
            ))
            if task != orders[v][-1]:
                # 标明本任务释放后的下一次出发；下一任务的 departure 携带新路线。
                events.append(_event(v, task, partner, "next_departure", "IDLE",
                                     available[v], point))

    return {
        "feasible": True, "reason": "", "orders": {v: list(orders.get(v, ())) for v in VEHICLES},
        "stations": stations, "start_times": {t: starts[t] for t in ALERTS},
        "end_times": {t: finishes[t] for t in ALERTS}, "latest_start_s": max(starts.values()),
        "total_distance_m": total_distance, "total_wait_s": total_wait,
        "vehicle_events": sorted(events, key=lambda e: (e["vehicle_id"], e["time_s"])),
    }


def evaluate_schedule(orders, stations, router, max_leg_m=None):
    """按任务依赖拓扑序回放五辆车，车辆到站后同步指向、等待、观测及分别复位。"""
    try:
        return _evaluate(orders, stations, router, max_leg_m)
    except InvalidSchedule as exc:
        return {"feasible": False, "reason": str(exc)}


def vehicle_state_at(schedule, vehicle_id, time_s):
    """在给定时刻推进已下发的第二问计划；同刻依次结算该车的各项事件。"""
    if not schedule["feasible"] or not isfinite(time_s) or time_s < 0 or vehicle_id not in VEHICLES:
        raise ValueError("回放需要可行排程、有效车辆和非负有限时刻")
    event = next(e for e in reversed(schedule["vehicle_events"])
                 if e["vehicle_id"] == vehicle_id and e["time_s"] <= time_s)
    elapsed = time_s - event["time_s"]
    x, y, pan, tilt = (event[k] for k in ("x_m", "y_m", "pan_deg", "tilt_deg"))
    if event["stage"] == "TRAVEL":
        path = event["route_xy_m"]
        x, y = path[-1]
        remaining = elapsed * SPEED
        for a, b in zip(path, path[1:]):
            length = dist(a, b)
            if remaining < length:
                fraction = remaining / length
                x, y = a[0] + fraction*(b[0]-a[0]), a[1] + fraction*(b[1]-a[1])
                break
            remaining -= length
    elif event["stage"] in ("SLEW", "RESET"):
        progress = SLEW_RATE_DEG_S * elapsed
        dp, dt = event["signed_pan_delta_deg"], event["tilt_delta_deg"]
        pan = degrees(_signed_turn(radians(pan + copysign(min(abs(dp), progress), dp))))
        tilt += copysign(min(abs(dt), progress), dt)
    active_task = "" if event["stage"] == "IDLE" else event["task_id"]
    return {"vehicle_id": vehicle_id, "task_id": active_task, "time_s": time_s,
            "stage": event["stage"], "x_m": x, "y_m": y, "pan_deg": pan, "tilt_deg": tilt}
