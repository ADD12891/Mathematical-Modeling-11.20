# B 题第 4 问复验意见 · 故障阻塞顺序

**复验版本为 `34d4dd3`，第 4 问仍待通过。** 上轮报告的 6 项问题均已修复，仓库自带 36 项测试通过，已发布的 209 个场景和 593 条策略结果也可复现。独立复验共 14 项，12 项通过。剩余 2 项出现在用题给车辆、告警构造的补充排程中。 [英文原题第 5 页][problem] [上轮验收意见][previous]

## 两个失败用例

从第 2 问冻结计划出发，让 V1 先做 T2、再与 V2 做 T3。V1 做 T3 时沿用其原车位 $(15,14)$。其余车和站点保留。`evaluate_schedule` 验证修改后的无故障方案可行，T1、T2、T3 原定启动时刻分别约为 2.332055、6.949461、16.737812 s。以下均检查保留原计划策略。 [第 2 问冻结计划][plan]

| 扰动 | 题意下的预期 | 实际输出 |
| --- | --- | --- |
| V2 在 5 s 故障，无断联。V2 只参加后续 T3。 | T1 保留成功，V1 与 V4 的 T2 仍在 6.949461 s 启动。T3 无法启动，完成率为 2/3。 | T2 也被取消，完成率误为 1/3。 |
| V4 在 5 s 故障，断联至 30 s。V4 参加 T2。 | T1 保留成功。V1 等待故障搭档，T2 无法启动，也不能在断联期间启动其后续 T3。完成率为 1/3。 | T3 仍被按原定 16.737812 s 计为启动，完成率误为 2/3。 |

## 原因与修正建议

- `_failure_blocked_tasks` 当前按受阻任务的健康搭档向所有共享任务传播，未检查该任务在本车队列中位于受阻任务之前还是之后。阻塞应沿每辆车的任务队列向后传播。已经完成或仍可执行的前置任务应保留。 [故障阻塞函数][blocking]
- `_locked_tasks` 在断联场景依据原定启动时刻锁定任务。建议先按故障和车辆队列回放至恢复时刻，再锁定两辆车实际到站、完成指向并已共同启动的任务。恢复后的实时重调度以这份锁定结果和当时的实际车辆状态为起点。 [锁定判定][locking] [原计划策略][retain]

## 最小复现代码

在仓库根目录的 Python 环境执行以下代码。

```python
import json
from pathlib import Path

from src.disturbance import Scenario, _base_policy
from src.routing import Router
from src.timeline import evaluate_schedule

snapshot = Path("results/question2/original_plan.json")
frozen = json.loads(snapshot.read_text(encoding="utf-8"))
orders = {car: list(tasks) for car, tasks in frozen["orders"].items()}
stations = {task: dict(crew) for task, crew in frozen["stations"].items()}
orders["V3"].remove("T3")
orders["V1"].append("T3")
stations["T3"].pop("V3")
stations["T3"]["V1"] = (15.0, 14.0)
plan = evaluate_schedule(orders, stations, Router())
assert plan["feasible"], plan["reason"]

later, _ = _base_policy(plan, Scenario("later-failure", failed_vehicle="V2"),
                        "retain_original")
outage, _ = _base_policy(
    plan, Scenario("earlier-failure", failed_vehicle="V4", recovery_time_s=30.0),
    "retain_original",
)
print(later["task_start_times_s"], outage["task_start_times_s"])
assert later["task_start_times_s"]["T2"] == plan["start_times"]["T2"]
assert later["task_start_times_s"]["T3"] is None
assert later["successful_alerts"] == 2
assert outage["task_start_times_s"]["T2"] is None
assert outage["task_start_times_s"]["T3"] is None
assert outage["successful_alerts"] == 1
```

[problem]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/34d4dd397a8c4555975bf0b5e8aa218b2d44e55e/2026_%E2%80%9CShuWei%20Cup%E2%80%9DB_Problem.pdf#page=5
[previous]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/34d4dd397a8c4555975bf0b5e8aa218b2d44e55e/%E4%BF%AE%E6%94%B9%E6%84%8F%E8%A7%81/B%E9%A2%98%E7%AC%AC%E5%9B%9B%E9%97%AE_%E6%9C%AC%E5%9C%B0%E6%B5%8B%E8%AF%95%E9%AA%8C%E6%94%B6%E6%84%8F%E8%A7%81.md
[plan]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/34d4dd397a8c4555975bf0b5e8aa218b2d44e55e/results/question2/original_plan.json
[blocking]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/34d4dd397a8c4555975bf0b5e8aa218b2d44e55e/src/disturbance.py#L130-L148
[locking]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/34d4dd397a8c4555975bf0b5e8aa218b2d44e55e/src/disturbance.py#L122-L128
[retain]: https://github.com/ADD12891/Mathematical-Modeling-11.20/blob/34d4dd397a8c4555975bf0b5e8aa218b2d44e55e/src/disturbance.py#L239-L258
