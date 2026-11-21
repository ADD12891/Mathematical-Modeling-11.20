# 第一问 · 运行与手动调试

第二问请使用[双站定位和三告警调度的运行与调试说明](QUESTION2.md)。

从仓库根目录运行以下命令。本次验证环境为 Python 3.12.10、NumPy 2.5.4 和 Matplotlib 3.11.2。

```powershell
python -m pip install -r src/requirements.txt
python -m src.question1
python -m unittest -v src.test_question1
```

主程序读取 [config.py](config.py) 中的原始告警、场地和建筑，运行三档网格与连续站点搜索，生成[收敛表](../results/tables/q1_convergence.csv)、[站点结果](../results/tables/q1_station.json)、[场地布局图](../results/figures/q1_layout.png)与[可观测区域图](../results/figures/q1_region.png)。调试输出可另存，例如 `python -m src.question1 --output-dir q1-debug`。

## 检查单个站点

在仓库根目录运行下面的命令即可单独检查 B1 东南角的外切站。预期车体净空为 0 m，停站、仰角、视线三类条件均通过，`observable` 为 `true`。

```powershell
python -m src.question1 --point 34.9 14.8
```

在 IDE 中将启动模块设为 `src.question1`，工作目录设为仓库根目录，参数填 `--point 34.9 14.8`。在 [geometry.py](geometry.py) 的 `check_station` 返回行设断点，可查看 `in_field`、`clearance`、`elevation_ok`、`clear`。用命令行调试同一点：

```powershell
python -m pdb -m src.question1 --point 34.9 14.8
```

```text
(Pdb) break src/geometry.py:80
(Pdb) continue
(Pdb) p (bool(in_field), float(clearance), bool(elevation_ok), bool(clear))
(True, 0.0, True, True)
```

## 核对整题结果

在 [question1.py](question1.py) 中，`sample_region` 以单元中心采样，`refine_station` 从最细网格的八个最近可行点分别做连续局部搜索，每次检验八个方向，找不到更近的可行点时将步长减半。`choose_station` 对候选点重新做点级检查。手动跟踪连续搜索时，去掉 `--point` 参数。

| 步长（m） | 可观测单元数 | 面积估计（m²） | 最小格点距离（m） |
|---:|---:|---:|---:|
| 0.5 | 22,432 | 5,608.0000 | 7.598761 |
| 0.25 | 89,839 | 5,614.9375 | 7.582223 |
| 0.125 | 358,908 | 5,607.9375 | 7.582162 |

仰角上限给出水平距离下界 $43/\tan(80^\circ)=7.582060170464\ldots$ m。T1 投影正东的站点约为 $(92.22307632184,70)$，通过几何检查且达到下界，因此按并列规则选为代表站，$Q=1/r\approx0.131890274875$ m⁻¹，仰角为 80°。调试时从目标和下界生成全精度坐标。把展示用的六位小数坐标重新输入，可能会跨过仰角边界。
