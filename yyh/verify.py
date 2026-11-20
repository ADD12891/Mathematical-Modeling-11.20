"""复算修订稿中的第1问静态区域图与第3问概率表。运行 python verify.py。"""
from pathlib import Path
from math import erfc, sqrt
import numpy as np
from numpy.polynomial.hermite import hermgauss
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Rectangle, Patch

BUILDINGS = [(22, 34, 16, 26, 20), (63, 79, 59, 71, 28),
             (10, 24, 65, 76, 12), (72, 86, 22, 34, 24), (41, 51, 38, 46, 32)]
TX, TY = 50 + 20 * sqrt(3), 70
R_MIN = 43 / np.tan(np.deg2rad(80))

def region(step):
    # 单元格中心作为采样点。乘以单元面积只用于近似面积。
    ticks = np.arange(step / 2, 100, step)
    x, y = np.meshgrid(ticks, ticks)
    r = np.hypot(TX - x, TY - y)
    visible = (x >= 1.5) & (y >= 1.5) & (x <= 98.5) & (y <= 98.5) & (r >= R_MIN)
    for left, right, bottom, top, height in BUILDINGS:
        dx = np.maximum(0, np.maximum(left - x, x - right))
        dy = np.maximum(0, np.maximum(bottom - y, y - top))
        visible &= np.hypot(dx, dy) >= 1.5
        # 只检查视线从相机升到建筑顶部这一段的水平投影。
        enter = np.zeros_like(x)
        leave = np.full_like(y, (height - 2) / 43)
        overlaps = np.ones_like(x, dtype=bool)
        for pos, target, low, high in ((x, TX, left, right), (y, TY, bottom, top)):
            delta = target - pos
            parallel = delta == 0
            overlaps &= ~parallel | ((low <= pos) & (pos <= high))
            for_division = np.where(parallel, 1.0, delta)
            a, b = (low - pos) / for_division, (high - pos) / for_division
            enter = np.maximum(enter, np.where(parallel, -np.inf, np.minimum(a, b)))
            leave = np.minimum(leave, np.where(parallel, np.inf, np.maximum(a, b)))
        visible &= ~(overlaps & (enter <= leave))
    return visible, r[visible].min()

def plot_region(mask):
    fig, ax = plt.subplots(figsize=(7.2, 6.5), layout="constrained")
    colors = ["#f0efec", "#91cec0"]
    ax.imshow(mask, origin="lower", extent=(0, 100, 0, 100), interpolation="nearest",
              cmap=ListedColormap(colors), vmin=0, vmax=1)
    for i, (left, right, bottom, top, _) in enumerate(BUILDINGS, 1):
        ax.add_patch(Rectangle((left, bottom), right-left, top-bottom, color="#414e5f"))
        ax.text((left+right)/2, (bottom+top)/2, f"B{i}", color="white", ha="center", va="center")
    t, = ax.plot(TX, TY, '^', color="#a63528", ms=8, label="T1 projection")
    p, = ax.plot(TX+R_MIN, TY, '*', color="#172143", ms=13, label="Optimal station")
    ax.set(title="T1 observable region", xlabel="East (m)", ylabel="North (m)", aspect="equal")
    ax.legend(handles=[Patch(color=colors[1], label="Observable"),
                       Patch(color=colors[0], label="Excluded"), t, p],
              loc="upper center", bbox_to_anchor=(.5, -.1), ncol=2)
    fig.savefig(Path(__file__).with_name("T1可观测区域.png"), dpi=170)
    plt.close(fig)

def probability_function(order):
    x, w = hermgauss(order)
    z = sqrt(2) * x
    weights = np.outer(w, w) / np.pi
    tail = np.vectorize(erfc, otypes=[float])
    def probability(sigma):
        angle = np.rad2deg(np.arctan2(-sigma*z[:, None], 40-sigma*z[None, :]))
        # 零偏与控制误差之和的标准差为1度；加上两侧最近的周期像。
        cond = np.zeros_like(angle)
        for k in (-1, 0, 1):
            mid = angle - 360*k
            cond += (tail((mid-5)/sqrt(2)) - tail((mid+5)/sqrt(2))) / 2
        return float((weights * cond).sum())
    return probability

if __name__ == "__main__":
    for h in (0.5, 0.25, 0.125):
        mask, r_grid = region(h)
        print("grid", h, "area", mask.sum()*h*h, "min distance", r_grid)
    plot_region(mask)
    for n in (24, 48, 80):
        prob = probability_function(n)
        lo, hi = 1.5, 1.65
        assert prob(lo) > .95 > prob(hi)
        while hi-lo > 1e-11:
            mid = (lo + hi) / 2
            if prob(mid) >= .95:
                lo = mid
            else:
                hi = mid
        print("order", n, "sigma bracket", (lo, hi), "s", (lo-.3)/.02)
        for distance in (66.83, 66.84, 66.92):
            print(distance, prob(.3+.02*distance))
