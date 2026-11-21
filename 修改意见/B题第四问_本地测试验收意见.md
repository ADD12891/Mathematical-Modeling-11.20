# B 题第 4 问本地测试验收意见

**验收结论为暂未通过。** 复核版本为远端 main 的 `e3cc885`。仓库原有 36 项测试全部通过。按英文原题新增的 12 项验收中，6 项通过、6 项失败。完整命令行在本地复现了 209 个扰动场景和 593 条策略结果。场景覆盖、策略配对、断联信息时序、严格早于 5 s 的锁定条件，以及按已记录联合启动事件复算完成率和最晚启动时刻，均通过。现有时间线仍有问题。 [英文原题第 5 页][problem] [场景与结果说明][docs]

## 优先修正事件时间线

1. **210 套时间线在故障车退出之后仍记录其动作。** 筛选事件表中 `scenario_id=failure-V3`、`vehicle_id=V3`，三种策略均记录 V3 在 5 s 故障后，于约 7.332055 s 结束观测并开始复位，于约 8.378680 s 完成复位并准备再次出发。T1 已在约 2.332055 s 联合启动，应保留成功分数。记录 5 s 的物理退出，并停止该车在 5 s 后的活动。该车的任务计分状态与车辆状态应分别更新。 [事件表][events] [原计划及锁定任务事件导出][failure]
2. **462 套时间线漏记了 5 s 前已经发生的动作。** 例如仅新增告警 `new-N1` 时，实时重调度漏列 V1 在 0 s 为 T2 出发的记录。两种主策略在 0 s 都应使用第二问同一份冻结计划。建议先保存冻结计划在 $[0,5)$ 内的实执行事件和 5 s 状态，再从扰动或恢复时刻接续可执行动作。逐车比较各策略的扰动前日志与原计划，并核对断联期间继续执行的已下发任务。 [冻结原计划][plan] [事件表][events] [原计划回放][failure] [重调度时间线拼接][splice]
3. **跳过故障任务对照漏了到站和完成指向。** 120 个单车观测启动缺少这些事件。例如 `failure-V1` 的 `skip_failed_task` 中，V2 和 V3 对 T3 都有约 15.777589 s 的观测启动记录，但没有同一任务的到站和完成指向记录。应补齐逐车阶段，复核两车均到站并完成指向后才启动联合观测。 [事件表][events] [跳过策略事件导出][skip]

## 补齐评分输出与文件校验

| 检查项 | 复现证据与修正建议 |
| --- | --- |
| 所有告警显式列出 | 140 条跳过对照结果漏列未启动的新增告警。例如 `new-N1-failure-V1 / skip_failed_task` 的分母已计入 N1，`task_start_times_s` 没有 N1 键。建议所有策略使用相同告警清单，未启动时显式保存空值。当前样本的完成率复算仍一致。 |
| 计分窗口下界 | 合成边界输入的三个启动时刻为 $-10^{-6}$、60 和 60.000001 s。按 $[0,60]$ 闭区间应仅计 60 s，当前函数计了两条。建议同时检查起止边界，并验收 0 s、60 s、负值和窗口外值。该负值问题由合成输入复现。 |
| 发布文件的 SHA-256 | 发布的 `scenarios.json` 为 LF 换行，清单中的字节哈希对应 CRLF 版本。本地重新生成的文件与发布场景的 JSON 内容一致。建议按固定 LF 写入再计算字节哈希，重新生成并从实际提交文件复核，或把规范化换行写入哈希约定及验证代码。 |

[评分明细][scores] [计分函数][scoring] [哈希清单][manifest]

## 重跑验收

在仓库根目录将结果重新生成到单独目录，再按上述场景和策略筛选事件。

```powershell
python -B -m src.disturbance --output-dir q4-review
python -B -m unittest -v src.test_disturbance
```

验收时逐项确认扰动前事件完整、故障车退出后无活动、联合启动有两车的到站与指向证据，并从事件和告警清单独立复算 $(\eta,t_{\rm last})$。

[problem]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/e3cc885a0b116283ce8878f314d9d9a33f76c61c/2026_%E2%80%9CShuWei%20Cup%E2%80%9DB_Problem.pdf#page=5
[docs]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/e3cc885a0b116283ce8878f314d9d9a33f76c61c/src/QUESTION4.md
[plan]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/e3cc885a0b116283ce8878f314d9d9a33f76c61c/results/question2/original_plan.json
[events]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/e3cc885a0b116283ce8878f314d9d9a33f76c61c/results/question4/event_log.csv
[scores]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/e3cc885a0b116283ce8878f314d9d9a33f76c61c/results/question4/scenario_scores.csv
[manifest]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/e3cc885a0b116283ce8878f314d9d9a33f76c61c/results/question4/manifest.json
[failure]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/e3cc885a0b116283ce8878f314d9d9a33f76c61c/src/disturbance.py#L161-L186
[skip]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/e3cc885a0b116283ce8878f314d9d9a33f76c61c/src/disturbance.py#L403-L442
[scoring]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/e3cc885a0b116283ce8878f314d9d9a33f76c61c/src/disturbance.py#L151-L158
[splice]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/e3cc885a0b116283ce8878f314d9d9a33f76c61c/src/disturbance.py#L330-L352
