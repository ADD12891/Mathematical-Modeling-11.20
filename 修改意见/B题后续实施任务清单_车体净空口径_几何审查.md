# B 题最终实现路线复审 · 车体净空口径 · 几何审查

复审基准为远端 main 的 `ef7f08d`。主路线及现有复算结果通过几何和定位核验。新增的《后续实施任务清单》§3.1 有一处变量定义冲突，建议在通用几何函数编码前修正。

## 1. 统一车心距离与车体净空的定义

主路线 §3.1 将车体净空定义为车心到建筑占地的最短距离减去车体半径，恰好外切时净空为 0 m。新清单 §3.1 写“精确外切的理论净空为 1.5 m”，并要求停站和整段路线使用 `clearance >= vehicle_radius - vehicle_clearance_tol`。该比较式中的 `clearance` 实际应取车心到建筑占地的距离。若函数按主路线返回车体净空，再按清单比较，便会重复扣除一个车体半径，有效阈值变为距离约 3 m。 [主路线 §3.1][route] [后续实施任务清单 §3.1][checklist]

建议把清单的定义和比较式写成以下形式。

- $d_{\min}$ 为车心到建筑占地的最短距离。停站取点到矩形的距离，路线取整段车心轨迹到矩形的最小距离。
- 车体净空为 $g=d_{\min}-r_v$，$r_v=1.5$ m。令 $\tau=10^{-12}$ m，判定式写为 $g\ge-\tau$。直接比较距离时用 $d_{\min}\ge r_v-\tau$。
- 外切验收同时断言 $d_{\min}=1.5$ m、$g=0$ m，以及停站或路线可行，数值量按相应的容差比较。

| 已有验收对象 | 车心最小距离（m） | 车体净空（m） | 正确结论 |
| --- | ---: | ---: | --- |
| B1 东南角外切站 $(34.9,14.8)$ | 1.500000 | 0.000000 | 可停靠 |
| T2 的 V3 直达西站路线 | 2.104320 | 0.604320 | 可通行 |
| T2 的 V5 直达北站路线 | 2.318790 | 0.818790 | 可通行 |

按主路线定义取得净空后再与 $1.5-\tau$ m 比较，会误删表中三个用例。现有 T1 脚本对车心距离使用半径减容差，结果正确。 [当前 T1 判定][verify] [T2 路线表][t2]

## 2. 本轮复算结果

- B1 东南角侵入、外切、分离三点，标量与批量结果均为不可观测、可观测、可观测。
- B2 北侧屋顶棱三点仍为遮挡、遮挡、可见。T1 三档网格与独立三维相交判定逐格一致，正东解析代表站仍可观测。
- T2 两站视线和路线均可行。当前一阶水平 RMS 的最优值为 0.143623358 m，两车用时分别为 5.369802 s、13.881594 s。 [实现路线 §5.2][t2]

[route]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/ef7f08dbebfc2afe5e47545d5f5db9f3bb3b9590/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md#L112-L124
[checklist]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/ef7f08dbebfc2afe5e47545d5f5db9f3bb3b9590/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/%E5%90%8E%E7%BB%AD%E5%AE%9E%E6%96%BD%E4%BB%BB%E5%8A%A1%E6%B8%85%E5%8D%95.md#L25-L44
[verify]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/ef7f08dbebfc2afe5e47545d5f5db9f3bb3b9590/yyh/verify.py#L17-L32
[t2]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/ef7f08dbebfc2afe5e47545d5f5db9f3bb3b9590/%E8%B5%9B%E5%89%8D%E5%87%86%E5%A4%87/%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF/B%E9%A2%98%E6%9C%80%E7%BB%88%E5%AE%9E%E7%8E%B0%E8%B7%AF%E7%BA%BF.md#L267-L287
