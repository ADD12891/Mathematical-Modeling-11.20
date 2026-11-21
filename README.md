# 数维杯 B 题 · 移动观测车辆协同定位

在仓库根目录执行以下命令。第四问直接读取第二问已提交的冻结计划。每个求解入口均支持 `--output-dir`，手动调试时可另存结果。

```powershell
python -m pip install -r src/requirements.txt
python -m src.question1
python -m src.question2
python -m src.question3
python -m src.disturbance
python -m unittest discover -v -s src -t .
```

[四问对照英文原题的复核记录](yyh/B题代码复核.md) · [实现路线](赛前准备/实现路线/B题最终实现路线.md) · [逐问调试说明](src/README.md) · [结果文件](results/)
