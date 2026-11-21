"""第二问入口。从仓库根目录运行 python -m src.question2。"""
import argparse
import csv
import hashlib
import json
import subprocess
from pathlib import Path

from . import config
from .assignment import AssignmentSearch, GRID_SPACING_M, candidate_stations
from .geometry import SEGMENT_T_TOL
from .localization import SINE_ROUNDOFF_TOL, solve_localization
from .routing import Router
from .timeline import LEG_TOL_M

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ("config.py", "geometry.py", "routing.py", "localization.py",
                "timeline.py", "assignment.py", "question2.py")


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8")


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0], lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, separators=(",", ":"))
                             if isinstance(value, (list, tuple)) else value
                             for key, value in row.items()})


def text_hash(path):
    # 规范化 Git 在 Windows 检出时可能转换的 CRLF。
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def source_revision():
    paths = ["src/" + name for name in SOURCE_FILES]
    try:
        revision = subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", *paths], cwd=ROOT,
            text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--", *paths], cwd=ROOT, text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return revision if revision and not dirty else None


def vehicle_rows(scenario, strategy, plan):
    """将逐事件日志整理为逐车逐任务表，空闲车也占一行。"""
    timestamps = ("departure", "arrival", "slew_start", "slew_end", "observation_start",
                  "observation_end", "reset_start", "reset_end", "next_departure")
    rows = []
    for car, tasks in plan["orders"].items():
        mileage = 0.0
        for task in tasks or [""]:
            events = {e["event"]: e for e in plan["vehicle_events"]
                      if e["vehicle_id"] == car and e["task_id"] == task}
            departure = events.get("departure", {})
            length = departure.get("leg_distance_m", 0.0)
            mileage += length
            station = plan["stations"][task][car] if task else config.VEHICLES[car]
            row = {"scenario": scenario, "strategy": strategy, "vehicle_id": car,
                   "task_id": task or "IDLE", "partner_id": departure.get("partner_id", ""),
                   "station_x_m": station[0], "station_y_m": station[1],
                   "route_xy_m": departure.get("route_xy_m", []),
                   "leg_distance_m": length, "cumulative_distance_m": mileage}
            row.update({name + "_s": events.get(name, {}).get("time_s") for name in timestamps})
            row["partner_wait_s"] = (row["observation_start_s"] - row["slew_end_s"]) if task else 0.
            rows.append(row)
    return rows


def run_assignment(router, out):
    sites = candidate_stations()
    search = AssignmentSearch(router, sites)
    experiments = [(scenario, cap, search.compare(cap)) for scenario, cap in
                   (("main", None), ("limited", config.SPEED * config.TRAVEL_LIMIT_S))]
    write_json(out / "candidates.json", sites)
    outputs = ["candidates.json"]
    summary = []
    vehicles = []
    for scenario, cap, plans in experiments:
        for strategy, plan in plans.items():
            vehicles.extend(vehicle_rows(scenario, strategy, plan))
            events_name = f"{scenario}_{strategy}_events.csv"
            write_csv(out / events_name, plan["vehicle_events"])
            outputs.append(events_name)
            summary.append({
                "scenario": scenario, "strategy": strategy, "leg_limit_m": cap,
                **{f"{task}_start_s": plan["start_times"][task] for task in config.ALERTS},
                **{f"{task}_end_s": plan["end_times"][task] for task in config.ALERTS},
                "latest_start_s": plan["latest_start_s"],
                "total_distance_m": plan["total_distance_m"], "total_wait_s": plan["total_wait_s"],
                "completed_tasks": len(plan["end_times"]), "task_count": len(config.ALERTS),
                "completion_rate": len(plan["end_times"]) / len(config.ALERTS),
            })
        if scenario == "main":
            write_json(out / "original_plan.json", plans["optimized"])
            outputs.append("original_plan.json")

    write_csv(out / "comparison.csv", summary)
    write_csv(out / "vehicle_tasks.csv", vehicles)
    outputs.extend(("comparison.csv", "vehicle_tasks.csv"))
    manifest = {
        "source_revision": source_revision(),
        "source_sha256": {name: text_hash(ROOT / "src" / name) for name in SOURCE_FILES},
        "output_sha256": {name: text_hash(out / name) for name in outputs},
        "hash_convention": "SHA-256 of UTF-8 text normalized to LF.",
        "original_plan": "original_plan.json",
        "inputs": {"field_size_m": config.FIELD_SIZE, "dispatch_center_xy_m": config.CENTER,
                   "vehicle_radius_m": config.VEHICLE_RADIUS, "camera_height_m": config.CAMERA_HEIGHT,
                   "buildings_xy_bounds_height_m": config.BUILDINGS, "vehicles_xy_m": config.VEHICLES,
                   "alerts_bearing_deg_range_height_m": config.ALERTS, "speed_m_s": config.SPEED,
                   "elevation_bounds_deg": config.ELEVATION_LIMITS_DEG,
                   "slew_rate_deg_s": config.SLEW_RATE_DEG_S, "observe_s": config.OBSERVATION_S,
                   "sensitivity_leg_limit_m": config.SPEED * config.TRAVEL_LIMIT_S,
                   "body_tol_m": config.BODY_TOL_M, "range_tol_m": config.DISTANCE_TOL_M,
                   "segment_t_tol": SEGMENT_T_TOL, "leg_limit_tol_m": LEG_TOL_M,
                   "bearing_sine_roundoff_tol": SINE_ROUNDOFF_TOL,
                   "router_steps_per_quarter_circle": router.corner_steps},
        "station_pool": {"grid_spacing_m": GRID_SPACING_M, "points_file": "candidates.json",
                         "points_per_target": {k: len(v) for k, v in sites.items()}},
        "optimization_scope": "All feasible assignments and acyclic orders on the saved station sets "
                              "and the route polylines returned by this router.",
        "random_seed": None,
    }
    write_json(out / "manifest.json", manifest)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", choices=("1", "2", "all"), default="all",
                        help="只运行第二问的第（1）或第（2）小问；默认两项都运行")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results" / "question2")
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    router = Router()
    if args.part in ("all", "1"):
        result = solve_localization(router)
        write_json(out / "localization.json", result)
        metrics = result["optimal"]["metrics"]
        print(f"2(1)  T2 RMS = {metrics['rms_m']:.9f} m; baseline = {metrics['baseline_m']:.6f} m")
    if args.part in ("all", "2"):
        for row in run_assignment(router, out):
            print(f"2(2)  {row['scenario']:7} {row['strategy']:9}  "
                  f"latest start = {row['latest_start_s']:.6f} s")
    print(f"Results: {out.resolve()}")


if __name__ == "__main__":
    main()
