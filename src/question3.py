"""第三问：解 95% 路程阈值、80 m 参数域，并分解一阶角度方差。"""
import argparse
import csv
import json
from math import degrees, floor, isfinite, radians, sqrt
from pathlib import Path
from statistics import NormalDist

from .bearing_error import critical_sigma, probability_model
from .question3_config import (
    Q3_BAND_DEG, Q3_CONTROL_STD_DEG, Q3_RANGE_M, Q3_REQUIRED_PROB,
    Q3_SIGMA_A_M, Q3_SIGMA_B, Q3_ZERO_STD_DEG, QUADRATURE_ORDERS,
)

DESIGN_DISTANCE_M = 80.0


def probability_at_distance(model, distance, a=Q3_SIGMA_A_M, b=Q3_SIGMA_B):
    if not all(isfinite(value) and value >= 0 for value in (distance, a, b)):
        raise ValueError("累计路程和参数 a、b 均须是非负有限数")
    sigma = a + b * distance
    if not isfinite(sigma):
        raise ValueError("a + b × distance 超过可表示的数值范围")
    command, actual = model.command(sigma), model.actual(sigma)
    return {
        "distance_m": distance, "a_m": a, "b": b, "coordinate_sigma_m": sigma,
        "command_probability": command, "command_meets_requirement": command >= Q3_REQUIRED_PROB,
        "actual_probability": actual, "actual_meets_requirement": actual >= Q3_REQUIRED_PROB,
    }


def variance_contributions(distance):
    """各向同性位置误差的一阶角度项只来自垂直于真实视线的分量。"""
    sigma = Q3_SIGMA_A_M + Q3_SIGMA_B * distance
    position = degrees(sigma / Q3_RANGE_M)**2
    zero, control = Q3_ZERO_STD_DEG**2, Q3_CONTROL_STD_DEG**2
    total = position + zero + control
    return {
        "distance_m": distance, "coordinate_sigma_m": sigma, "position_var_deg2": position,
        "zero_offset_var_deg2": zero, "control_var_deg2": control, "total_var_deg2": total,
        "actual_position_share": position / total, "actual_zero_offset_share": zero / total,
        "actual_control_share": control / total,
    }


def first_order_sigma(gimbal_variance):
    z = NormalDist().inv_cdf((1 + Q3_REQUIRED_PROB) / 2)
    position_variance = (Q3_BAND_DEG / z)**2 - gimbal_variance
    return Q3_RANGE_M * radians(sqrt(position_variance))


def nominal_distance(sigma):
    return (sigma - Q3_SIGMA_A_M) / Q3_SIGMA_B


def solve_question3():
    convergence = []
    previous = {}
    for order in QUADRATURE_ORDERS:
        model = probability_model(order)
        for metric, probability in (("command", model.command), ("actual", model.actual)):
            low, high = critical_sigma(probability)
            sigma = (low + high) / 2
            distance = nominal_distance(sigma)
            convergence.append({
                "metric": metric, "order": order, "passing_sigma_m": low, "failing_sigma_m": high,
                "critical_distance_m": distance,
                "change_from_previous_order_m": distance - previous[metric] if metric in previous else None,
            })
            previous[metric] = distance

    # 用最高阶求积在厘米格点直接判断。连续根四舍五入可能落在不达标一侧。
    limits = {}
    for row in convergence[-2:]:
        metric = row["metric"]
        probability = model.command if metric == "command" else model.actual
        sigma = (row["passing_sigma_m"] + row["failing_sigma_m"]) / 2
        cm = floor(row["critical_distance_m"] * 100)
        def at_cm(n):
            return probability(Q3_SIGMA_A_M + Q3_SIGMA_B * (n / 100))
        if at_cm(cm + 1) >= Q3_REQUIRED_PROB:
            cm += 1
        elif at_cm(cm) < Q3_REQUIRED_PROB:
            cm -= 1
        p_pass, p_fail = at_cm(cm), at_cm(cm+1)
        if not p_pass >= Q3_REQUIRED_PROB > p_fail:
            raise ArithmeticError("相邻厘米的达标概率未包住临界值")
        gimbal_variance = 0.0 if metric == "command" else Q3_ZERO_STD_DEG**2 + Q3_CONTROL_STD_DEG**2
        linear_sigma = first_order_sigma(gimbal_variance)
        limits[metric] = {
            "critical_distance_m": row["critical_distance_m"],
            "max_passing_cm_grid_m": cm / 100, "probability_at_max_cm_grid": p_pass,
            "next_cm_grid_m": (cm+1) / 100, "probability_at_next_cm_grid": p_fail,
            "first_order_critical_distance_m": nominal_distance(linear_sigma),
            "nonlinear_probability_at_first_order_limit": probability(linear_sigma),
            "parameter_domain_at_80m": {
                "condition": "a >= 0, b >= 0, a + 80*b <= sigma_star, where P(sigma_star) = 0.95",
                "critical_sigma_m": sigma, "passing_sigma_m": row["passing_sigma_m"],
                "failing_sigma_m": row["failing_sigma_m"],
                "passing_b_if_a_is_zero": row["passing_sigma_m"] / DESIGN_DISTANCE_M,
                "passing_b_if_a_is_nominal": (row["passing_sigma_m"] - Q3_SIGMA_A_M) / DESIGN_DISTANCE_M,
            },
        }

    result = {
        "inputs": {"range_m": Q3_RANGE_M, "nominal_a_m": Q3_SIGMA_A_M, "nominal_b": Q3_SIGMA_B,
                   "zero_offset_std_deg": Q3_ZERO_STD_DEG, "control_std_deg": Q3_CONTROL_STD_DEG,
                   "angle_band_deg": Q3_BAND_DEG, "required_probability": Q3_REQUIRED_PROB,
                   "quadrature_orders": QUADRATURE_ORDERS},
        "limits": limits,
        "nominal_at_80m": probability_at_distance(model, DESIGN_DISTANCE_M),
    }
    return result, convergence, [variance_contributions(s) for s in (0., 40., DESIGN_DISTANCE_M)]


def plot_parameter_region(path, limits):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(6.9, 4.7), layout="constrained")
    canvas = FigureCanvasAgg(fig)
    ax = fig.subplots()
    for metric, label, color in (("command", "Commanded bearing only", "#b7d9ea"),
                                 ("actual", "Both criteria", "#65b6a3")):
        intercept = limits[metric]["parameter_domain_at_80m"]["critical_sigma_m"]
        ax.fill([0, intercept, 0], [0, 0, intercept / DESIGN_DISTANCE_M],
                facecolor=color, edgecolor="#23495a", linewidth=1.1, label=label)
    ax.scatter(Q3_SIGMA_A_M, Q3_SIGMA_B, marker="x", s=65, color="#8c2f21",
               label="Nominal (0.3, 0.02)", zorder=3)
    ax.set(xlim=(0, None), ylim=(0, None), xlabel="Initial position standard deviation a (m)",
           ylabel="Position error growth b (m/m)", title="95% feasible parameter regions at 80 m")
    ax.legend(loc="upper right", frameon=False)
    canvas.print_figure(path, dpi=160)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "results" / "question3")
    parser.add_argument("--distance", type=float, help="只验证给定累计路程（m）的两种达标概率")
    parser.add_argument("--a", type=float, help="单点验证的初始位置标准差（m）")
    parser.add_argument("--b", type=float, help="单点验证的位置误差增长率（m/m）")
    args = parser.parse_args()
    if args.distance is not None:
        try:
            point = probability_at_distance(probability_model(QUADRATURE_ORDERS[-1]), args.distance,
                                            Q3_SIGMA_A_M if args.a is None else args.a,
                                            Q3_SIGMA_B if args.b is None else args.b)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(point, indent=2, allow_nan=False))
        return
    if args.a is not None or args.b is not None:
        parser.error("--a 和 --b 用于 --distance 的单点验证")
    result, convergence, contributions = solve_question3()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n",
                                      encoding="utf-8")
    for name, rows in (("quadrature.csv", convergence), ("variance_contributions.csv", contributions)):
        with (out / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0], lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    plot_parameter_region(out / "feasible_80m.png", result["limits"])
    for metric, values in result["limits"].items():
        print(f"{metric}: critical distance {values['critical_distance_m']:.5f} m; "
              f"last passing centimeter {values['max_passing_cm_grid_m']:.2f} m")
    print(f"Results: {out.resolve()}")


if __name__ == "__main__":
    main()
