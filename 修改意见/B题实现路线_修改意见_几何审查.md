# B 题实现路线修改意见 · 几何审查

审查版本为远端 `main` 的 `3ec6557`（2026 年 11 月 20 日）。依据英文题面第 2–4 页，对照《B题最终实现路线》《B题解题总稿_修订稿》和 C 模块的 G1–G3、E1–E2 验收条目。本轮建议优先落实 T1 仰角边界、T2 点级可见性和双站误差三项验收。 [英文 B 题][problem] [主实现路线][route] [修订稿][draft] [C 模块验收条目][cases]

## 1. 几何模型与可观测区域

**G1 · 补一个能识别圆角缓冲的用例。** 车辆模型与题面一致。车心与建筑地面矩形的距离至少为 1.5 m，恰好外切可行。B1 西侧三个用例的净空依次为 0、−0.001 和 +0.001 m，原预期成立。从 `(18,21)` 到 `(38,21)` 的直线路段中段穿过 B1，两端均可停。建议加测车心 `(20.8,14.8)`，它到 B1 西南角的距离为 1.697056 m，净空约 0.197056 m，应判为可停。这个用例能发现把矩形四边外扩后直接当作精确禁区的误判。路径验收应逐段检查整段车心轨迹与建筑地面矩形的最小距离。 [实现路线 §3][geometry] [G1][cases]

**G2 · 增加斜视线相切和真实 T2 越顶用例。** 当前 T1 脚本固定了 43 m 高差，它在本场景确实能正确处理三维遮挡。建议把后续目标的可见性验收统一到相机至目标的三维闭线段与原建筑闭长方体的相交判定，输入使用实际端点高度。车体外切与视线接触采用各自的规则。可直接复用以下预期值。 [实现路线 §3][geometry] [T1 复算脚本][verifyRegion]

| 用例 | 输入与独立预期 |
| --- | --- |
| B1 西侧屋顶棱 | 仅放置 B1。相机在 `(17,21,2)`，目标为 `(27,21,H)`。当 $H=37.98,38,38.02$ m 时，视线到达西面 $x=22$ m 的高度依次为 19.99、20.00、20.01 m，应依次判为遮挡、遮挡、无遮挡。 |
| 真实 T2 越过 B3 | 车心取 `(6,78)`，相机高 2 m。仰角约 77.274218°，车体最近建筑净空约 2.972136 m。视线的水平投影在 $x=10$ m 进入 B3，此时视线高约 28.248510 m，超过 12 m 屋顶，也未碰到其他建筑。此点应判为 T2 可观测。 |

**G3 · 固化 80° 边界及并列站点的数值验收。** T1 的最佳水平距离为 $43/\tan 80^\circ\approx7.582060170$ m。独立复核确认正东站和与正东成 45° 的东北站都达到下界、车体可停且三维视线无遮挡。把正东站点从解析式生成时可以通过本次严格比较。但把修订稿表中的 `(92.223076,70)` 读回程序时，水平距离比下界短约 $3.22\times10^{-7}$ m，仰角约为 $80.000000416^\circ$，严格比较会筛掉该点。当前脚本直接画出星标，网格中心检查也没有覆盖这个解析点。建议由原始告警和解析式生成两个全精度点，调用与候选筛选相同的点级判定，验证两种输入顺序均选正东。为等限比较明确定义单位与浮点容差，例如在本题尺度下用 $10^{-9}$ m 作为该水平距离比较的测试起点，并验证 $r_{\min}\pm10^{-4}$ m 的两点分别得到正确的区内、区外分类。坐标表的舍入值仅用于展示。 [实现路线 §4.2][t1] [修订稿 T1 表][draftT1] [网格判定和图中星标][verifyRegion]

**T1 图的复算结果。** 将现有脚本的三个网格掩膜与独立的三维线段相交实现逐格比较，三档均为零差异。面积为网格面积估计，解析最短距离由 G3 单独验证。 [T1 复算脚本][verifyRegion] [已提交的 T1 图][plot]

| 网格步长（m） | 可观测采样点 | 面积估计（m²） | 网格最小水平距离（m） |
| ---: | ---: | ---: | ---: |
| 0.5 | 22,432 | 5,608.0000 | 7.598761 |
| 0.25 | 89,839 | 5,614.9375 | 7.582223 |
| 0.125 | 358,908 | 5,607.9375 | 7.582162 |

## 2. 双站定位误差和站对复核

**E1 · 把误差数值和退化阈值纳入验收。** 独立按矩阵法和闭式分别计算，确认修订路线的平面一阶 RMS 公式正确。现有 E1 主要核对基线，建议同时校验协方差和 RMS。令两站到目标投影的水平距离均为 10 m，取 $\sigma_\theta=\pi/180$ rad。交会角 90° 时，基线为 14.142136 m，协方差两对角元均为 0.030461742 m²、非对角元为 0，RMS 为 0.246826830 m。交会角 120° 时，基线为 17.320508 m，RMS 为 0.285011073 m。可另加不等距用例 $r_1=8$ m、$r_2=20$ m、$\gamma=110^\circ$，基线为 23.946742 m，RMS 为 0.400083408 m，用于核对两站分别按各自的水平距离传播。共线 0° 与 180° 应在矩阵求解前识别。实现时固定采用的近退化判据及阈值，并检查阈值变化是否改变最佳站对。各站通过可见性检查后，再对站对判定交会退化。 [实现路线 §5.1][error] [E1][cases]

**第 2 问（1）· 用实际路线验收 30 s 限制。** 定位结果交付时，两辆车各自应附起点至分配站点的路径点列、逐段几何判定、累计长度和行驶时间。题设速度为 3 m/s，因此每辆车的实际通行路线至多 90 m。欧氏距离可用作排除下界，粗网格超限的临界候选应按当前路线说明复查。T2 的双站最优结果及可通行路线仍待求解后核验。 [英文 B 题第 4 页][problem4] [实现路线 §5.2][search] [修订稿第 2 问][draftT2]

**E2 · 概率边界复算通过。** 直接调用现有概率函数做 48、80 阶求积。80 阶下，66.83 m 为 95.001721%，66.84 m 为 94.999350%，两阶对达标的判断一致。 [复算脚本][probability] [修订稿概率结论][draftE2]

本次运行了现有脚本的数值部分，并查看已提交的 T1 图。车体边界、三维视线和双站协方差由独立计算核对。模块联调和小组冻结仍以队员后续的代码验收记录为准。

[problem]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/2026_%E2%80%9CShuWei%20Cup%E2%80%9DB_Problem.pdf
[route]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md
[draft]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/yyh/B%E9%A2%98%E8%A7%A3%E9%A2%98%E6%80%BB%E7%A8%BF_%E4%BF%AE%E8%AE%A2%E7%A8%BF.md
[cases]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/yyh/C%E6%A8%A1%E5%9D%97%E6%95%B4%E5%90%88%E4%B8%8E%E4%BF%AE%E6%94%B9%E8%AE%B0%E5%BD%95.md#L35-L41
[geometry]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md#L87-L123
[t1]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md#L144-L164
[draftT1]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/yyh/B%E9%A2%98%E8%A7%A3%E9%A2%98%E6%80%BB%E7%A8%BF_%E4%BF%AE%E8%AE%A2%E7%A8%BF.md#L43-L51
[verifyRegion]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/yyh/verify.py#L17-L51
[plot]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/yyh/T1%E5%8F%AF%E8%A7%82%E6%B5%8B%E5%8C%BA%E5%9F%9F.png
[error]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md#L177-L234
[problem4]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/2026_%E2%80%9CShuWei%20Cup%E2%80%9DB_Problem.pdf#page=4
[search]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md#L236-L247
[draftT2]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/yyh/B%E9%A2%98%E8%A7%A3%E9%A2%98%E6%80%BB%E7%A8%BF_%E4%BF%AE%E8%AE%A2%E7%A8%BF.md#L53-L71
[probability]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/yyh/verify.py#L59-L71
[draftE2]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/3ec6557cd0629c7fb07795d6269cff35c68bab89/yyh/B%E9%A2%98%E8%A7%A3%E9%A2%98%E6%80%BB%E7%A8%BF_%E4%BF%AE%E8%AE%A2%E7%A8%BF.md#L129-L139
