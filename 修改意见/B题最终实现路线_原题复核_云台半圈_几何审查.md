# B 题最终实现路线原题复核 · 云台半圈与误差符号 · 几何审查

复审基准为 `053111a`。对照英文原题第 3–5 页，T1 的三档可观测网格、T2 的标称最优站对与 0.5° 仰角余量的备选站对、第 3 问两项概率临界值均复算一致。上轮的边界站重选意见已落实于主路线 §5.2。下面两处可在实现和论文定稿前澄清。 [原题][problem] [主路线的边界处理][boundary]

## 1. 为恰好 180° 的水平转向指定方向

原题第 3 页规定两轴可同时转动，最大角速度分别为 30°/s。主路线 §2.3 取最小绝对角差，能唯一确定转向总耗时。恰差 180° 时，两条方向相反的最短转动轨迹等长，§6.1 的连续状态回放和 §8 的扰动时刻快照还需要确定带符号的转角。 [原题第 3 页][problem3] [云台转向][gimbal] [状态回放][state] [扰动时刻快照][events]

**可复核的题内用例。** 假设 V4 在 0 s 从 $(88,68)$ 直达 T1 正东代表站 $(92.2230763218\ldots,70)$。该路段可通行，到站用时约 1.557576 s。云台从正东水平转向正西、仰角 80°，完成指向约在 7.557576 s。在 5 s 的快照中，两种最短转动方向对应水平角约 $+103.272734^\circ$ 或 $-103.272734^\circ$，两者仰角均为 80°。

建议在 §2.3 明确水平角差映射到半开区间 $[-\pi,\pi)$，恰好半圈时固定取 $-\pi$。在每次转向开始时保存带符号的转角，回放按该转角及已过时间推进。按此约定，上述快照水平角应为 $-103.272734^\circ$。建议将其与已有的 90° 耗时用例并列验收，初始方案和重调度共用同一规则。

## 2. 修订稿沿用主路线的两个角差下标

原题第 4 页用 $\Delta\psi$ 表示指令方位与真实方位之差。主路线 §7 已分别用 $\Delta\psi_{\mathrm{cmd}}$ 和 $\Delta\psi_{\mathrm{act}}$ 定义指令角和实际指向。数值修订稿的“角差的定义”仍把无下标的 $\Delta\psi$ 用作含云台误差的总偏差。建议该段和后续公式沿用两个下标，使摘要、正文和 95% 达标事件一一对应。两组连续临界值仍分别为 73.93607 m 和 66.83726 m。 [题面第 4 页][problem4] [主路线 §7][angles] [修订稿当前定义][draft]

[problem]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/053111af49a639ee300e47ea283cac03881cb696/2026_%E2%80%9CShuWei%20Cup%E2%80%9DB_Problem.pdf
[problem3]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/053111af49a639ee300e47ea283cac03881cb696/2026_%E2%80%9CShuWei%20Cup%E2%80%9DB_Problem.pdf#page=3
[problem4]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/053111af49a639ee300e47ea283cac03881cb696/2026_%E2%80%9CShuWei%20Cup%E2%80%9DB_Problem.pdf#page=4
[boundary]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/053111af49a639ee300e47ea283cac03881cb696/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md#L269-L297
[gimbal]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/053111af49a639ee300e47ea283cac03881cb696/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md#L91-L110
[state]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/053111af49a639ee300e47ea283cac03881cb696/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md#L312-L324
[events]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/053111af49a639ee300e47ea283cac03881cb696/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md#L469-L481
[angles]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/053111af49a639ee300e47ea283cac03881cb696/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md#L402-L416
[draft]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/053111af49a639ee300e47ea283cac03881cb696/yyh/B%E9%A2%98%E8%A7%A3%E9%A2%98%E6%80%BB%E7%A8%BF_%E4%BF%AE%E8%AE%A2%E7%A8%BF.md#L94-L105
