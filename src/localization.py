"""第 2（1）问：双站水平定位协方差、数值搜索和连续最优值验证。"""
from itertools import combinations, product
from math import atan2, degrees, dist, hypot, isclose, radians, sqrt, tan
from sys import float_info

import numpy as np

from .config import (
    ALERTS, BEARING_STD_DEG, BODY_TOL_M, CAMERA_HEIGHT, ELEVATION_LIMITS_DEG,
    FIELD_SIZE, SPEED, TRAVEL_LIMIT_S, VEHICLES, VEHICLE_RADIUS, target_position,
)
from .geometry import check_station, minimum_elevation_range
from .routing import route_length, segment_clearance

# 仅判别双精度舍入下不可分辨的共线；题面没有最小交会角。
SINE_ROUNDOFF_TOL = 16 * float_info.epsilon
GRID_STEP_M = 10.0
FINAL_LOCAL_STEP_M = 0.001


def _bearing(target, station):
    dx, dy = target[0] - station[0], target[1] - station[1]
    radius = hypot(dx, dy)
    return (dx / radius, dy / radius, radius) if radius else (0., 0., 0.)


def _signed_sine(u, v):
    return u[0]*v[1] - u[1]*v[0]


def is_nondegenerate_pair(target, station1, station2):
    """仅检验两条水平方位线可唯一交会；停站、时间等条件由调用方检查。"""
    sine = _signed_sine(_bearing(target, station1), _bearing(target, station2))
    return abs(sine) > SINE_ROUNDOFF_TOL


def pair_metrics(target, station1, station2):
    """返回一阶水平定位协方差与 RMS；无唯一交点时误差字段为 None。"""
    u, v = _bearing(target, station1), _bearing(target, station2)
    sine = abs(_signed_sine(u, v))
    cosine = u[0]*v[0] + u[1]*v[1]
    metrics = {
        "ranges_m": [u[2], v[2]], "baseline_m": dist(station1, station2),
        "angle_deg": degrees(atan2(sine, cosine)) if u[2] and v[2] else None,
        "abs_sin_angle": sine, "condition_number": None, "covariance_m2": None,
        "rms_m": None, "closed_form_rms_m": None,
    }
    if sine <= SINE_ROUNDOFF_TOL:
        return metrics
    # 对 A^{-1} diag(r1 sigma, r2 sigma) 求解，乘其转置得到协方差。
    normals = np.array([[-u[1], u[0]], [-v[1], v[0]]])
    sigma = radians(BEARING_STD_DEG)
    propagation = np.linalg.solve(normals, np.diag([u[2]*sigma, v[2]*sigma]))
    covariance = propagation @ propagation.T
    metrics.update(condition_number=(1 + abs(cosine))/sine,
                   covariance_m2=covariance.tolist(), rms_m=sqrt(float(np.trace(covariance))),
                   closed_form_rms_m=sigma*hypot(u[2], v[2])/sine)
    return metrics


def _feasible_routes(router, station):
    reachable = []
    limit = SPEED * TRAVEL_LIMIT_S
    for vehicle, start in VEHICLES.items():
        if dist(start, station) > limit:
            continue
        route = router.route(start, station)
        if route is not None:
            length = route_length(route)
            if length <= limit:
                reachable.append((vehicle, length, route))
    return reachable


def _assign(stations, routes_to):
    """固定站对的精度与车辆身份无关，按最晚到站、总长和编号打破并列。"""
    assignments = ((a, b) for a, b in product(*(routes_to(p) for p in stations)) if a[0] != b[0])
    return min(assignments,
               key=lambda pair: (max(pair[0][1], pair[1][1]), pair[0][1]+pair[1][1],
                                 pair[0][0], pair[1][0]), default=None)


def _solution(target, stations, assignments):
    metrics = pair_metrics(target, *stations)
    rows = []
    for point, (vehicle, length, path) in zip(stations, assignments):
        checked = check_station(target, point)
        segments = []
        for a, b in zip(path, path[1:]):
            gap = float(segment_clearance(a, b))
            inside = all(VEHICLE_RADIUS <= c <= FIELD_SIZE-VEHICLE_RADIUS for c in (*a, *b))
            valid = inside and gap >= -BODY_TOL_M
            segments.append({"from_xy_m": a, "to_xy_m": b, "length_m": dist(a, b),
                             "building_clearance_m": gap, "passable": valid})
        if not checked.observable or not all(s["passable"] for s in segments):
            raise ValueError("选定站点或路线未通过几何复核")
        rows.append({
            "vehicle": vehicle, "start_xy_m": VEHICLES[vehicle], "station_xy_m": point,
            "route_xy_m": path, "segments": segments, "length_m": length,
            "travel_time_s": length/SPEED, "station_clearance_m": float(checked.clearance_m),
            "elevation_deg": float(checked.elevation_deg), "sight_clear": bool(checked.sight_clear),
        })
    return {"assignments": rows, "metrics": metrics}


def _visible(target, points):
    points = list(points)
    mask = check_station(target, np.asarray(points).T).observable
    return [p for p, keep in zip(points, mask) if keep]


def _best_pair(target, pairs, routes_to):
    # 先按闭式 RMS 排序。遇到第一个存在两辆不同车辆各自可行的站对即可结束。
    ranked = []
    sigma = radians(BEARING_STD_DEG)
    for a, b in pairs:
        u, v = _bearing(target, a), _bearing(target, b)
        sine = abs(_signed_sine(u, v))
        if sine > SINE_ROUNDOFF_TOL:
            ranked.append((sigma*hypot(u[2], v[2])/sine, a, b))
    for _, a, b in sorted(ranked):
        assignments = _assign((a, b), routes_to)
        if assignments:
            return (a, b), assignments
    return None


def _numerical_search(target, routes_to):
    ticks = np.arange(GRID_STEP_M/2, FIELD_SIZE, GRID_STEP_M).tolist()
    points = _visible(target, product(ticks, repeat=2))
    winner = _best_pair(target, combinations(points, 2), routes_to)
    if not winner:
        return {"grid_spacing_m": GRID_STEP_M, "observable_grid_points": len(points),
                "grid": None, "refined": None}
    grid = _solution(target, *winner)
    step = GRID_STEP_M / 2
    while step >= FINAL_LOCAL_STEP_M:
        neighborhoods = []
        for x, y in winner[0]:
            offsets = (-step, 0., step)
            neighbors = ((x+dx, y+dy) for dx, dy in product(offsets, repeat=2))
            neighborhoods.append(_visible(target, neighbors))
        # 中心仍在邻域内，上一层可行解始终保留。
        winner = _best_pair(target, product(*neighborhoods), routes_to)
        step /= 2
    return {"grid_spacing_m": GRID_STEP_M, "observable_grid_points": len(points),
            "last_refinement_step_m": 2*step, "grid": grid, "refined": _solution(target, *winner)}


def solve_localization(router):
    """求解 T2 的两站题，所有路程约束只计行驶时间。返回可写入 JSON 的报告。"""
    target = target_position(ALERTS["T2"])
    routes = {}

    def routes_to(point):
        if point not in routes:
            routes[point] = _feasible_routes(router, point)
        return routes[point]

    def west_north(radius):
        x, y = target[:2]
        points = ((x-radius, y), (x, y+radius))
        if len(_visible(target, points)) < 2:
            return None
        assignment = _assign(points, routes_to)
        return _solution(target, points, assignment) if assignment else None

    search = _numerical_search(target, routes_to)
    radius = minimum_elevation_range(target)
    lower = radians(BEARING_STD_DEG)*sqrt(2)*radius
    witness = west_north(radius)
    optimal = witness or search["refined"]
    if optimal is None:
        raise ValueError("在所搜集合内未找到满足双车行驶时限的站对")
    attained = witness is not None and isclose(witness["metrics"]["rms_m"], lower,
                                                rel_tol=0., abs_tol=1e-12)
    margin_deg = 0.5
    margin_radius = (target[2]-CAMERA_HEIGHT) / tan(radians(ELEVATION_LIMITS_DEG[1]-margin_deg))
    margin = west_north(margin_radius)
    if margin:
        margin["elevation_margin_deg"] = margin_deg
        margin["rms_increase_percent"] = 100*(margin["metrics"]["rms_m"]/optimal["metrics"]["rms_m"]-1)
    return {
        "target_xyz_m": target, "bearing_std_deg": BEARING_STD_DEG, "travel_limit_s": TRAVEL_LIMIT_S,
        "sine_roundoff_tol": SINE_ROUNDOFF_TOL, "optimal": optimal, "margin_example": margin,
        "certificate": {"range_lower_bound_m": radius, "rms_lower_bound_m": lower,
                        "bound_attained": attained,
                        "scope": "continuous first-order horizontal RMS" if attained else "searched pairs only",
                        "derivation": "r1,r2 >= d and abs(sin(gamma)) <= 1, so RMS >= sigma*sqrt(2)*d."},
        "search": search,
        "degeneracy": ("The sine cutoff covers roundoff only. A small resolved sine increases "
                       "both condition number and RMS. Reselect a pair with a larger crossing sine."),
        "boundary": ("At the nominal 80-degree limit the perpendicular pair has zero elevation margin "
                     "and condition number 1. For the margin example move away from the target, "
                     "then recheck both sight lines and travel paths."),
        "route_limit_note": ("Only verified routes of at most 30 s are used. A longer detour "
                             "alone does not prove unreachability."),
    }
