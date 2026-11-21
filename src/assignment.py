"""第二问（2）：固定候选集，比较最近分配和以最晚启动时刻为目标的搜索。"""
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations, permutations

import numpy as np

from .config import ALERTS, FIELD_SIZE, OBSERVATION_S, SPEED, VEHICLES, target_position
from .geometry import is_station_observable
from .localization import is_nondegenerate_pair
from .routing import route_length
from .timeline import LEG_TOL_M, evaluate_schedule, slew_times

GRID_SPACING_M = 2.0
TASKS = tuple(ALERTS)
CARS = tuple(VEHICLES)
CAR_PAIRS = tuple(combinations(range(len(CARS)), 2))


def candidate_stations():
    """围绕五个车位选取单目标和双目标共视站，再在原坐标中细化边界。"""
    axis = np.arange(GRID_SPACING_M / 2, FIELD_SIZE, GRID_SPACING_M)
    x, y = np.meshgrid(axis, axis)
    grid = np.column_stack((x.ravel(), y.ravel()))
    targets = {t: target_position(ALERTS[t]) for t in TASKS}
    visible = {t: is_station_observable(target, grid.T) for t, target in targets.items()}
    origins = np.array(list(VEHICLES.values()))
    start_visible = {t: is_station_observable(target, origins.T) for t, target in targets.items()}
    sites = set(VEHICLES.values())
    groups = [(t,) for t in TASKS] + list(combinations(TASKS, 2))
    for group in groups:
        eligible = grid[np.logical_and.reduce([visible[t] for t in group])]
        if not len(eligible):
            continue
        for index, origin in enumerate(origins):
            distances = np.linalg.norm(eligible - origin, axis=1)
            nearest = eligible[np.argmin(distances)]
            sites.add(tuple(nearest))
            if all(start_visible[t][index] for t in group):
                continue
            # 保留可行侧端点。此处只筛选站点，两站之间的实际行驶路线另算。
            outside, inside = origin, nearest
            for _ in range(20):
                midpoint = (outside + inside) / 2
                if all(is_station_observable(targets[t], midpoint) for t in group):
                    inside = midpoint
                else:
                    outside = midpoint
            sites.add(tuple(inside))

    sites = np.array(sorted(sites))
    return {t: [tuple(map(float, p)) for p in sites[is_station_observable(target, sites.T)]]
            for t, target in targets.items()}


@dataclass(frozen=True)
class State:
    positions: tuple
    free: tuple
    latest: float = 0.0
    mileage: float = 0.0
    decisions: tuple = ()


class AssignmentSearch:
    def __init__(self, router, candidates):
        self.router = router
        self.sites = candidates
        self.slews = {}
        self.valid_pairs = {}
        for task, points in candidates.items():
            target = target_position(ALERTS[task])
            self.slews[task] = np.array([slew_times(target, p) for p in points])
            self.valid_pairs[task] = np.array([
                [is_nondegenerate_pair(target, a, b) for b in points] for a in points
            ])
        # 缓存限于本次求解实例，两套实验共享同一条路线。
        self.lengths_from = lru_cache(maxsize=None)(self._lengths_from)

    def _lengths_from(self, origin, task):
        lengths = []
        for site in self.sites[task]:
            route = self.router.route(origin, site)
            lengths.append(route_length(route) if route is not None else np.inf)
        return np.array(lengths)

    def extensions(self, state, task, cars, max_leg, incumbent, nearest, final):
        """为给定车对选择站对。可行性及排序键均使用全精度数值。"""
        i, j = cars
        if max(state.latest, state.free[i], state.free[j]) > incumbent:
            return []
        a = self.lengths_from(state.positions[i], task)
        b = self.lengths_from(state.positions[j], task)
        slew, reset = self.slews[task].T
        ready_a = state.free[i] + a / SPEED + slew
        ready_b = state.free[j] + b / SPEED + slew
        starts = np.maximum(ready_a[:, None], ready_b[None, :])
        valid = self.valid_pairs[task] & np.isfinite(starts) & (starts <= incumbent)
        if max_leg is not None:
            valid &= (a[:, None] <= max_leg + LEG_TOL_M) & (b[None, :] <= max_leg + LEG_TOL_M)
        u, v = np.nonzero(valid)
        if not len(u):
            return []
        start = starts[u, v]
        length = a[u] + b[v]
        if nearest:
            ranking = np.lexsort((v, u, start, length))
        elif final:
            ranking = np.lexsort((v, u, length, np.maximum(state.latest, start)))
        else:
            ranking = np.lexsort((v, u, length, start))
        # 中间层穷举所有能追平当前最优的站对。末层无后续任务，直接取最优。
        if nearest or final:
            ranking = ranking[:1]
        extensions = []
        for k in ranking:
            p, q = self.sites[task][u[k]], self.sites[task][v[k]]
            positions, free = list(state.positions), list(state.free)
            positions[i], positions[j] = p, q
            end = float(start[k]) + OBSERVATION_S
            free[i], free[j] = float(end + reset[u[k]]), float(end + reset[v[k]])
            decision = (task, cars, (p, q))
            next_state = State(tuple(positions), tuple(free), max(state.latest, float(start[k])),
                               state.mileage + float(length[k]), (*state.decisions, decision))
            key = (float(length[k]), float(start[k]), cars, p, q)
            extensions.append((key, next_state))
        return extensions

    def score(self, state, max_leg):
        orders = {v: [] for v in CARS}
        stations = {}
        for task, cars, points in state.decisions:
            stations[task] = {}
            for car, point in zip(cars, points):
                vehicle = CARS[car]
                orders[vehicle].append(task)
                stations[task][vehicle] = point
        result = evaluate_schedule(orders, stations, self.router, max_leg_m=max_leg)
        if not result["feasible"]:
            raise ValueError(f"候选方案未通过统一评分器：{result['reason']}")
        if abs(result["latest_start_s"] - state.latest) > 1e-8:
            raise ValueError("搜索与独立时间线的最晚启动时刻不一致")
        return result

    def compare(self, max_leg=None):
        initial = State(tuple(VEHICLES[v] for v in CARS), (0.0,) * len(CARS))
        baseline = initial
        for task in TASKS:
            options = [choice for cars in CAR_PAIRS for choice in
                       self.extensions(baseline, task, cars, max_leg, np.inf, True, False)]
            if not options:
                raise ValueError(f"最近分配在 {task} 找不到可行车对和站对")
            baseline = min(options, key=lambda choice: choice[0])[1]

        best = baseline
        # 所有无环任务先后关系都有至少一种拓扑顺序。互不占车的任务仍然并行。
        def search(state, remaining):
            nonlocal best
            task, *later = remaining
            for cars in CAR_PAIRS:
                options = self.extensions(state, task, cars, max_leg, best.latest, False, not later)
                for _, child in options:
                    if later:
                        search(child, later)
                    elif (child.latest, child.mileage) < (best.latest, best.mileage):
                        best = child

        for order in permutations(TASKS):
            search(initial, order)
        return {"nearest": self.score(baseline, max_leg), "optimized": self.score(best, max_leg)}
