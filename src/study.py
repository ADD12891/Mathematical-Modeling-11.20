"""联跑四问，并用同一次运行的站位、里程、冻结计划和扰动结果生成论文对照表。"""
import argparse
import csv
import json
import subprocess
import sys
from collections import Counter, defaultdict
from math import hypot, isclose
from pathlib import Path

from .bearing_error import probability_model
from .config import ALERTS, target_position
from .disturbance import run_question4
from .geometry import check_station
from .localization import pair_metrics
from .question2 import text_hash, write_csv
from .question3 import probability_at_distance

ROOT = Path(__file__).resolve().parents[1]
SCHEMES = (("precision", "只优化 T2 精度"), ("nearest", "三告警最近分配"), ("optimized", "三告警时间优化"))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def percent(value):
    # 连续高斯噪声有尾部，显示精度内接近 1 时避免写成绝对保证。
    return ">99.999999%" if value > 0.99999999 else f"{value:.6%}"


def observation_rows(root):
    q1 = read_json(root / "tables/q1_station.json")
    single = read_json(root / "question2/localization.json")["optimal"]
    q3 = read_json(root / "question3/summary.json")
    inputs = q3["inputs"]
    model = probability_model(inputs["quadrature_orders"][-1])
    groups = defaultdict(list)
    for leg in single["assignments"]:
        groups["precision", "T2"].append((leg["vehicle"], leg["station_xy_m"], leg["length_m"]))
    for row in read_rows(root / "question2/vehicle_tasks.csv"):
        if row["scenario"] == "main" and row["task_id"] != "IDLE":
            point = (float(row["station_x_m"]), float(row["station_y_m"]))
            groups[row["strategy"], row["task_id"]].append(
                (row["vehicle_id"], point, float(row["cumulative_distance_m"])))

    observations = []
    for (strategy, task), crew in groups.items():
        if len(crew) != 2 or crew[0][0] == crew[1][0]:
            raise ValueError(f"{strategy}/{task} 的双车席位不完整")
        target = target_position(ALERTS[task])
        pair = pair_metrics(target, crew[0][1], crew[1][1])
        if pair["rms_m"] is None:
            raise ValueError(f"{strategy}/{task} 的双车站位退化")
        for car, point, mileage in crew:
            check = check_station(target, point)
            if not check.observable:
                raise ValueError(f"{strategy}/{task}/{car} 未通过第一问的可观测判定")
            ref = probability_at_distance(model, mileage, inputs["nominal_a_m"], inputs["nominal_b"])
            quality = 1 / float(check.distance_m)
            observations.append({
                "strategy": strategy, "task": task, "vehicle": car,
                "station_x_m": point[0], "station_y_m": point[1],
                "horizontal_range_m": float(check.distance_m), "quality_per_m": quality,
                "fraction_of_q1_best_T1_quality": quality / q1["quality_per_m"] if task == "T1" else None,
                "pair_angle_deg": pair["angle_deg"], "pair_abs_sin": pair["abs_sin_angle"],
                "pair_range_norm_m": hypot(*pair["ranges_m"]), "pair_rms_m": pair["rms_m"],
                "ratio_to_best_T2_rms": pair["rms_m"] / single["metrics"]["rms_m"] if task == "T2" else None,
                "cumulative_distance_m": mileage, "q3_reference_range_m": inputs["range_m"],
                "q3_command_probability": ref["command_probability"],
                "q3_actual_probability": ref["actual_probability"],
                "q3_actual_distance_margin_m": q3["limits"]["actual"]["critical_distance_m"] - mileage,
            })
    return observations


def accuracy_changes(observations):
    """固定测角方差时，RMS 比值可精确分解为距离因子与交会角因子之积。"""
    pairs = {(r["strategy"], r["task"]): r for r in observations}
    rows = []
    for task in ALERTS:
        old, new = pairs["nearest", task], pairs["optimized", task]
        range_ratio = new["pair_range_norm_m"] / old["pair_range_norm_m"]
        angle_ratio = old["pair_abs_sin"] / new["pair_abs_sin"]
        rms_ratio = new["pair_rms_m"] / old["pair_rms_m"]
        if not isclose(range_ratio * angle_ratio, rms_ratio, rel_tol=1e-9, abs_tol=0):
            raise ValueError(f"{task} 的几何分解与协方差计算的 RMS 比值不符")
        rows.append({"task": task, "range_factor": range_ratio, "angle_factor": angle_ratio,
                     "rms_ratio": rms_ratio})
    return rows


def compare_scenarios(scores):
    """在相同场景内先比较成功数。达到相同完成率时再比较启动时刻。"""
    pairs = defaultdict(dict)
    for row in scores:
        policies = pairs[row["scenario_id"]]
        if row["strategy"] in policies:
            raise ValueError(f"重复的场景策略行 {row['scenario_id']} / {row['strategy']}")
        policies[row["strategy"]] = row
    rows = []
    for scenario, policies in pairs.items():
        old, new = policies["retain_original"], policies["realtime_reschedule"]
        if old["alert_count"] != new["alert_count"]:
            raise ValueError(f"{scenario} 的计分分母不同")
        delta = int(new["successful_alerts"]) - int(old["successful_alerts"])
        old_t, new_t = float(old["t_last_s"]), float(new["t_last_s"])
        if delta:
            outcome = "more_starts" if delta > 0 else "fewer_starts"
        elif isclose(new_t, old_t, abs_tol=1e-9, rel_tol=0):
            outcome = "tie"
        else:
            outcome = "earlier" if new_t < old_t else "later"
        rows.append({"scenario_id": scenario, "group": "duration" if old["outage_interpretation"] == "duration" else "main",
                     "alerts": int(old["alert_count"]), "retained_starts": int(old["successful_alerts"]),
                     "rescheduled_starts": int(new["successful_alerts"]), "retained_t_last_s": old_t,
                     "rescheduled_t_last_s": new_t, "outcome": outcome})
    return rows


def write_study(root):
    q2 = root / "question2"
    digest = text_hash(q2 / "original_plan.json")
    q4 = read_json(root / "question4/manifest.json")
    if digest != q4["source_plan_sha256"]:
        raise ValueError("第四问使用的计划与本轮第二问输出不同")
    observations = observation_rows(root)
    changes = accuracy_changes(observations)
    paired = compare_scenarios(read_rows(root / "question4/scenario_scores.csv"))
    if len(paired) != q4["scenario_count"]:
        raise ValueError("配对场景数与第四问清单不符")
    comparison = {r["strategy"]: r for r in read_rows(q2 / "comparison.csv") if r["scenario"] == "main"}
    out = root / "integration"
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "observations.csv", observations)
    write_csv(out / "accuracy_changes.csv", changes)
    write_csv(out / "paired_scenarios.csv", paired)
    lines = ["# 四问联算与论文对照", "",
             "## 单站、站对与三告警调度", "",
             "先用第一问的可观测判定逐站复核，再用第二问（1）的独立 1° 测角模型评价实际站对。表中的最晚启动取全部三个告警，RMS 只取 T2。", "",
             "|方案|T1 两席的 Q / 第一问最优 Q|T2 交会角（°）|T2 一阶水平 RMS（m）|最晚启动（s）|",
             "|---|---|---:|---:|---:|"]
    for key, label in SCHEMES:
        crew = [r for r in observations if r["strategy"] == key]
        t2 = next(r for r in crew if r["task"] == "T2")
        quality = "、".join(f"{r['vehicle']} {r['fraction_of_q1_best_T1_quality']:.1%}" for r in crew if r["task"] == "T1")
        latest = f"{float(comparison[key]['latest_start_s']):.6f}" if key in comparison else "—"
        lines.append(f"|{label}|{quality or '—'}|{t2['pair_angle_deg']:.3f}|{t2['pair_rms_m']:.6f}|{latest}|")
    lines += ["", "时间优化结果适用于保存的候选站集和路线。以下按告警配对，检查最晚启动提前时，哪些任务获得收益，哪些任务付出代价。RMS 用两车各自独立的 1° 方位测量误差计算，数值越小表示一阶定位精度越高。", "",
              "|告警|最近分配启动（s）|时间优化启动（s）|最近分配 RMS（m）|时间优化 RMS（m）|",
              "|---|---:|---:|---:|---:|"]
    rms = {(r["strategy"], r["task"]): r["pair_rms_m"] for r in observations}
    for task in ALERTS:
        start = task + "_start_s"
        lines.append(f"|{task}|{float(comparison['nearest'][start]):.6f}|{float(comparison['optimized'][start]):.6f}|"
                     f"{rms['nearest', task]:.6f}|{rms['optimized', task]:.6f}|")
    lines += ["", "在相同的测角误差假设下，一阶水平 RMS 正比于 √(r₁² + r₂²) / |sin γ|。因此，时间优化相对最近分配的 RMS 比值，等于两站距离合成值之比乘以交会角因子 |sin γ旧| / |sin γ新|。因子小于 1 表示该项缩小误差。", "",
              "|告警|距离因子|交会角因子|两项乘积（RMS 比值）|", "|---|---:|---:|---:|"]
    for r in changes:
        lines.append(f"|{r['task']}|{r['range_factor']:.4f}|{r['angle_factor']:.4f}|{r['rms_ratio']:.4f}|")
    lines += ["", "[未舍入的误差分解](accuracy_changes.csv)",
              "", "## 同一段路程在第三问误差条件下的表现", "",
              "把各车到该席位时的累计行程 s 代回第三问，保持车到目标水平距离为 40 m，并沿用题给误差分布。下表选取每种方案累计里程最长的席位。概率对应 40 m 参照情景，实际站位的水平距离另列在逐席表中。余量为实际指向的 95% 临界里程减 s。", "",
              "|方案|车辆与任务|累计里程（m）|指令角达标率|实际指向达标率|里程余量（m）|",
              "|---|---|---:|---:|---:|---:|"]
    for key, label in SCHEMES:
        row = max((r for r in observations if r["strategy"] == key), key=lambda r: r["cumulative_distance_m"])
        lines.append(f"|{label}|{row['vehicle']} / {row['task']}|{row['cumulative_distance_m']:.3f}|"
                     f"{percent(row['q3_command_probability'])}|{percent(row['q3_actual_probability'])}|{row['q3_actual_distance_margin_m']:.3f}|")
    lines += ["", "同里程并列时表中仅展示一个席位。[逐席站位、精度和里程](observations.csv)",
              "", "## 从冻结计划到扰动回放", "",
              f"本轮第二问原计划与第四问输入的文本 SHA-256 均为 `{digest}`。每个场景内比较保留原计划和实时重调度，先看启动数，再看最晚启动。两个实验组在部分恢复时刻有重合，计数分别报告。", "",
              "|场景组|样本数|启动数增加|同完成率且提前|持平|同完成率且推迟|启动数减少|", "|---|---:|---:|---:|---:|---:|---:|"]
    for group, label in (("main", "主实验"), ("duration", "时长敏感性")):
        counts = Counter(r["outcome"] for r in paired if r["group"] == group)
        lines.append(f"|{label}|{counts.total()}|{counts['more_starts']}|{counts['earlier']}|{counts['tie']}|{counts['later']}|{counts['fewer_starts']}|")
    lines += ["", "主实验中按题定顺序退步的场景如下。", ""]
    losses = [r for r in paired if r["group"] == "main" and r["outcome"] in ("later", "fewer_starts")]
    for r in losses:
        lines.append(f"- `{r['scenario_id']}` 的启动数由 {r['retained_starts']} 变为 {r['rescheduled_starts']}，"
                     f"最晚时刻由 {r['retained_t_last_s']:.6f} s 变为 {r['rescheduled_t_last_s']:.6f} s。")
    if not losses:
        lines.append("本组无退步场景。")
    lines += ["", "场景计数描述所选样本。新增告警和恢复时刻的概率分布需有额外依据，不能由胜负次数推出。",
              "[逐场景配对成绩](paired_scenarios.csv) · [车辆事件日志](../question4/event_log.csv)", ""]
    (out / "研究结果.md").write_text("\n".join(lines), encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results")
    root = parser.parse_args().output_dir.resolve()
    for module, out in (("question1", root), ("question2", root / "question2"), ("question3", root / "question3")):
        subprocess.run([sys.executable, "-m", f"src.{module}", "--output-dir", str(out)], cwd=ROOT, check=True)
    run_question4(root / "question4", plan_dir=root / "question2")
    print(f"四问联算完成，论文对照表位于 {write_study(root)}")


if __name__ == "__main__":
    main()
