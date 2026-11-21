"""题一几何图和题二逐车时间线。绘图数据由对应的求解器生成。"""
from matplotlib import rc_context
from matplotlib.font_manager import fontManager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from matplotlib.patches import Patch, Rectangle

from .config import BUILDINGS, CENTER, FIELD_SIZE


def save_schedule_figure(path, rows):
    """两套三告警方案共用时间刻度。横条宽度直接取自逐车任务表。"""
    names = set(fontManager.get_font_names())
    family = next((f for f in ("Microsoft YaHei", "Noto Sans CJK SC", "SimHei") if f in names), None)
    phases = (("departure_s", "arrival_s", "行驶", "Travel", "#387b99"),
              ("slew_start_s", "slew_end_s", "转向", "Slew", "#b37635"),
              ("slew_end_s", "observation_start_s", "等搭档", "Wait", "#d6dbe0"),
              ("observation_start_s", "observation_end_s", "共同观测", "Observe", "#27644e"),
              ("reset_start_s", "reset_end_s", "复位", "Reset", "#797393"))
    main = [r for r in rows if r["scenario"] == "main"]
    vehicles = sorted({r["vehicle_id"] for r in main})
    with rc_context({"font.family": family or "DejaVu Sans"}):
        fig = Figure(figsize=(9, 6), dpi=180)
        canvas = FigureCanvasAgg(fig)
        axes = fig.subplots(2, 1, sharex=True)
        fig.subplots_adjust(left=.09, right=.97, top=.90, bottom=.17, hspace=.32)
        for ax, strategy, cn, en in zip(axes, ("nearest", "optimized"), ("最近分配", "时间优化"),
                                        ("Nearest assignment", "Time-optimized assignment")):
            subset = [r for r in main if r["strategy"] == strategy and r["task_id"] != "IDLE"]
            latest = max(float(r["observation_start_s"]) for r in subset)
            for r in subset:
                y = vehicles.index(r["vehicle_id"])
                for start, end, _, _, color in phases:
                    a, b = float(r[start]), float(r[end])
                    if b > a:
                        ax.barh(y, b-a, left=a, height=.65, color=color, zorder=2)
                middle = (float(r["observation_start_s"]) + float(r["observation_end_s"])) / 2
                ax.text(middle, y, r["task_id"], ha="center", va="center", color="white", fontsize=9)
            ax.axvline(latest, color="#263746", ls=":", lw=1.25, zorder=1)
            ax.set_title(cn if family else en, loc="left", fontsize=13)
            ax.set_title((f"最晚启动  {latest:.3f} s" if family else f"Latest start  {latest:.3f} s"),
                         loc="right", fontsize=10)
            ax.set_yticks(range(len(vehicles)), vehicles)
            ax.set_ylim(len(vehicles)-.5, -.5)
            ax.grid(axis="x", color="#e5e8eb", linewidth=.7, zorder=0)
            ax.spines[["left", "top", "right"]].set_visible(False)
            ax.tick_params(axis="y", length=0)
        axes[-1].set_xlim(left=0)
        axes[-1].set_xlabel("从同时告警起算的时间（s）" if family else "Time since simultaneous alerts (s)")
        handles = [Patch(color=color, label=cn if family else en) for _, _, cn, en, color in phases]
        fig.legend(handles=handles, loc="lower center", ncol=len(handles), frameon=False)
        canvas.print_figure(path)


def save_figures(output_dir, mask, target, station):
    """在 output_dir 下保存布局图及单元中心网格的可观测域图。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    visible_color, excluded_color = "#8ed1bb", "#eaeef1"
    for show_region, filename in ((False, "q1_layout.png"), (True, "q1_region.png")):
        figure = Figure(figsize=(7.2, 7.9))
        canvas = FigureCanvasAgg(figure)
        ax = figure.subplots()
        figure.subplots_adjust(left=0.13, right=0.95, bottom=0.18, top=0.89)
        legend_handles = []
        if show_region:
            # extent 给出网格的外边界；第一行对应南侧的半格中心。
            ax.imshow(mask, origin="lower", extent=(0, FIELD_SIZE, 0, FIELD_SIZE),
                      cmap=ListedColormap([excluded_color, visible_color]),
                      vmin=0, vmax=1, interpolation="nearest")
            legend_handles.extend((Patch(color=visible_color, label="Observable"),
                                   Patch(color=excluded_color, label="Excluded")))
            area = mask.sum() * FIELD_SIZE ** 2 / mask.size
            subtitle = (f"Area estimate: {area:,.4f} m²   |   "
                        f"Grid spacing: {FIELD_SIZE / mask.shape[1]:g} m")
            title = "T1 observable region"
        else:
            ax.set_facecolor("#f6f7f8")
            center, = ax.plot(*CENTER, "P", color="#66727d", markersize=9,
                              label="Dispatch center", zorder=4)
            legend_handles.append(center)
            subtitle = f"Building footprints and heights   |   Target altitude: {target[2]:g} m"
            title = "T1 field layout"

        for i, (left, right, bottom, top, height) in enumerate(BUILDINGS, start=1):
            ax.add_patch(Rectangle((left, bottom), right - left, top - bottom,
                                   facecolor="#394755", edgecolor="#263642", zorder=2))
            label = f"B{i}" if show_region else f"B{i}\n{height:g} m"
            ax.text((left + right) / 2, (bottom + top) / 2, label, color="white",
                    ha="center", va="center", fontsize=9, linespacing=1.05, zorder=3)

        ax.plot([target[0], station[0]], [target[1], station[1]], color="#35566a",
                linewidth=1, zorder=3)
        target_marker, = ax.plot(*target[:2], "^", markersize=9, color="#b5442c",
                                 markeredgecolor="white", label="T1 projection", zorder=4)
        station_marker, = ax.plot(*station, "*", markersize=15, color="#203c64",
                                  markeredgecolor="white", label="Optimal station", zorder=4)
        legend_handles.extend((target_marker, station_marker))
        ax.set(xlim=(0, FIELD_SIZE), ylim=(0, FIELD_SIZE), aspect="equal",
               xlabel="East (m)", ylabel="North (m)")
        ax.tick_params(colors="#394755")
        figure.suptitle(title, fontsize=18, fontweight="bold", color="#243748", y=0.975)
        figure.text(0.5, 0.925, subtitle, ha="center", fontsize=10, color="#455664")
        figure.legend(handles=legend_handles, loc="lower center", bbox_to_anchor=(0.5, 0.055),
                      ncol=len(legend_handles), frameon=False, columnspacing=1.1, fontsize=10)
        canvas.print_figure(output_dir / filename, dpi=180)
