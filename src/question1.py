"""第一问入口。在仓库根目录运行 python -m src.question1。"""
import argparse
import csv
import json
from math import atan2, dist, isfinite
from pathlib import Path

import numpy as np

from .config import DISTANCE_TOL_M, FIELD_SIZE, GRID_STEPS, t1_position
from .geometry import check_station, is_station_observable, minimum_elevation_range


def sample_region(target, step):
    """网格单元中心采样。按可见中心数估计面积，保留最近的可行网格点。"""
    if not isfinite(step) or step <= 0:
        raise ValueError("网格步长必须是正的有限数")
    count = round(FIELD_SIZE / step)
    if count < 1 or not np.isclose(count * step, FIELD_SIZE, rtol=0, atol=1e-10):
        raise ValueError("网格步长应能将场地等分成完整单元")
    ticks = (np.arange(count) + 0.5) * step
    check = check_station(target, (ticks[None, :], ticks[:, None]))
    mask = check.observable
    rows, cols = np.nonzero(mask)
    if not len(rows):
        raise ValueError(f"步长 {step} m 未采到可观测点")
    radii = check.distance_m[rows, cols]
    nearest = np.argsort(radii, kind="stable")[:8]
    seeds = np.column_stack((ticks[cols[nearest]], ticks[rows[nearest]]))
    row = {
        "step_m": step, "observable_points": len(rows),
        "area_estimate_m2": len(rows) * step**2, "grid_min_distance_m": float(radii.min()),
    }
    return mask, row, seeds


def refine_station(target, seed, step):
    """从可行网格点开始，沿八个方向轮询可行的连续坐标，缩小搜索步长。"""
    point = np.array(seed, dtype=float)
    radius = dist(point, target[:2])
    offsets = np.deg2rad(np.arange(0, 360, 45))
    while step > 1e-8:
        toward = atan2(target[1] - point[1], target[0] - point[0])
        angles = toward + offsets
        trial = point + step * np.column_stack((np.cos(angles), np.sin(angles)))
        check = check_station(target, trial.T)
        distances = np.where(check.observable, check.distance_m, np.inf)
        index = distances.argmin()
        if distances[index] < radius - 1e-12:
            point = trial[index]
            radius = distances[index]
        else:
            step /= 2
    return tuple(map(float, point))


def choose_station(target, candidates):
    """对连续候选点复查三项约束。距离并列时优先最接近解析正东站的点。"""
    points = np.asarray(candidates, dtype=float).reshape(-1, 2)
    check = check_station(target, points.T)
    feasible = check.observable
    if not np.any(feasible):
        raise ValueError("所有候选站均未通过精确几何复核")
    bound = minimum_elevation_range(target)
    minimum = max(bound, float(np.min(check.distance_m[feasible])))
    tied = points[feasible & (check.distance_m <= minimum + DISTANCE_TOL_M)]
    east = (target[0] + bound, target[1])
    best = min(tied, key=lambda p: (dist(p, east), p[0], p[1]))
    return tuple(map(float, best))


def describe_station(target, station):
    check = check_station(target, station)
    return {
        "station_xy_m": list(station),
        "horizontal_distance_m": float(check.distance_m),
        "elevation_deg": float(check.elevation_deg),
        "center_inside_field_limits": bool(check.in_field),
        "building_clearance_m": float(check.clearance_m),
        "elevation_allowed": bool(check.elevation_ok),
        "line_of_sight_clear": bool(check.sight_clear),
        "observable": bool(check.observable),
    }


def solve_question1():
    target = t1_position()
    lower_bound = minimum_elevation_range(target)
    east = (target[0] + lower_bound, target[1])
    east_feasible = bool(is_station_observable(target, east))
    convergence = []
    for step in sorted(GRID_STEPS, reverse=True):
        mask, row, seeds = sample_region(target, step)
        convergence.append(row)

    # 取最细网格的八个最近点独立细化，保留数值搜索结果以便和解析下界对照。
    refined = [refine_station(target, seed, step) for seed in seeds]
    local_best = choose_station(target, refined)
    candidates = [*refined, east] if east_feasible else refined
    best = choose_station(target, candidates)
    result = describe_station(target, best)
    result.update({
        "target_xyz_m": list(target),
        "quality_per_m": 1 / result["horizontal_distance_m"],
        "lower_bound_m": lower_bound,
        "best_refined_xy_m": list(local_best),
        "best_refined_distance_m": dist(local_best, target[:2]),
        "optimality": "global_lower_bound_attained" if best == east else "best_feasible_found",
        "tie_break": "Prefer the feasible due-east station at the analytic lower bound.",
    })
    return result, convergence, mask


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--point", type=float, nargs=2, metavar=("X", "Y"),
                        help="只打印给定车心坐标的约束检查结果")
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "results",
                        help="生成 tables 和 figures 的目录（默认仓库根目录下的 results）")
    args = parser.parse_args()
    if args.point is not None:
        if not all(map(isfinite, args.point)):
            parser.error("站点坐标必须是有限数值")
        print(json.dumps(describe_station(t1_position(), args.point), ensure_ascii=False, indent=2))
        return

    from .plots import save_figures

    result, convergence, mask = solve_question1()
    tables = args.output_dir / "tables"
    figures = args.output_dir / "figures"
    tables.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    (tables / "q1_station.json").write_text(output + "\n", encoding="utf-8")
    with (tables / "q1_convergence.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=convergence[0].keys())
        writer.writeheader()
        writer.writerows(convergence)
    save_figures(figures, mask, result["target_xyz_m"], result["station_xy_m"])
    print(output)
    print(f"第一问的收敛表、最优站点和两张图已写入 {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
