# CS240 Rail Line Planning

这是 CS240 城市轨道线路规划实验仓库。当前代码主线已经整理到
`line_network_model/`：先从 OSM/OpenHouse 风格数据生成需求与候选站点，
再用价值连接目标函数构造基线线路，并输出 CSV、SVG 和对比表。

## 环境

本项目使用 `uv` 管理 Python 环境和命令。

```powershell
uv sync
```

运行脚本时请使用：

```powershell
uv run python .\line_network_model\run_all.py
```

不要直接用 `pip install` 或裸 `python`，除非是在调试 Python 运行环境本身。

## 目录结构

```text
.
├── line_network_model/          # 当前主模型：需求准备、站点选择、线路规划
│   ├── 00_prepare_demand_data.py
│   ├── 01_select_stations.py
│   ├── 02_fetch_existing_metro_reference.py
│   ├── run_baseline.py
│   ├── run_genetic_baseline.py
│   ├── run_all.py
│   ├── line_model_config.py     # 研究区域、候选点数量、固定中心价值等配置
│   ├── objective_config.py      # 建设成本、转角惩罚、端点惩罚等目标函数参数
│   ├── model_spec.md            # 形式化模型说明
│   └── output/                  # 生成结果
├── doc/                         # 课程文档、proposal、论文材料副本
├── papers/                      # 文献 PDF、摘录文本和阅读笔记
├── cache/                       # Overpass/OSM 等中间缓存
├── pyproject.toml
└── uv.lock
```

## 一键运行

完整本地流程：

```powershell
uv run python .\line_network_model\run_all.py
```

`run_all.py` 当前依次执行：

1. `00_prepare_demand_data.py`：准备人口、建筑、功能点、就业和吸引点数据。
2. `01_select_stations.py`：生成候选站点。
3. `02_fetch_existing_metro_reference.py`：获取既有地铁线作为参考底图。
4. `run_baseline.py`：运行贪心价值树基线并导出结果。

遗传算法基线单独运行：

```powershell
uv run python .\line_network_model\run_genetic_baseline.py
```

## 分步运行

如果只想重跑某一段，可以按下面顺序执行：

```powershell
uv run python .\line_network_model\00_prepare_demand_data.py
uv run python .\line_network_model\01_select_stations.py
uv run python .\line_network_model\02_fetch_existing_metro_reference.py
uv run python .\line_network_model\run_baseline.py
uv run python .\line_network_model\run_genetic_baseline.py
```

通常在修改 `line_model_config.py` 后，需要至少从
`01_select_stations.py` 开始重跑；如果修改研究区域 `BBOX` 或人口/OSM 数据逻辑，
建议从 `00_prepare_demand_data.py` 开始重跑。

## 模型主线

当前实验把线路规划拆成两层：

1. 候选站点生成：从人口中心、就业中心、交通枢纽和商业中心生成候选站点，并为每个站点计算 `total_value`。
2. 线路/网络选择：在候选站点之间选择连边，使高价值站点对在网络内连接得更短，同时惩罚建设长度、过急转角和开放端点。

核心目标函数的实现参数在：

- `line_network_model/line_model_config.py`
- `line_network_model/objective_config.py`

更完整的数学定义见：

- `line_network_model/README.md`
- `line_network_model/model_spec.md`

## 主要输出

结果默认写入 `line_network_model/output/`。

站点与需求层：

- `01_candidate_stations_table.csv`
- `01_station_selection.svg`
- `01a_population_centers.svg`
- `01b_job_centers.svg`
- `01c_transport_centers.svg`
- `01d_commercial_centers.svg`

基线线路：

- `04_baseline_greedy_value_tree.svg`
- `04_baseline_line.svg`
- `05_baseline_comparison.csv`

遗传算法基线：

- `04_baseline_genetic_value_tree.svg`
- `06_genetic_baseline_summary.csv`

既有地铁参考：

- `00_existing_metro_reference.svg`

注意：`output/demand_data/` 中的 GeoJSON 文件仍作为内部中间数据使用，
但当前 workflow 不再导出候选站点、中心点、线路网络或既有地铁参考的 GeoJSON 结果。

## 常用修改点

- 修改研究区域：编辑 `line_network_model/line_model_config.py` 里的 `BBOX`。
- 修改候选点数量：调整 `POPULATION_CENTER_COUNT` 和 `JOB_CENTER_COUNT`。
- 修改中心合并尺度：调整 `CENTER_MERGE_RADIUS_M`。
- 修改交通枢纽是否强制纳入：调整 `TRANSPORT_CENTERS_REQUIRED`。
- 修改目标函数权重：编辑 `line_network_model/objective_config.py`。
- 修改遗传算法规模：编辑 `line_network_model/run_genetic_baseline.py` 里的种群、代数、交叉率和变异率。

## 文档与文献

- `doc/proposal/`：课程 proposal 的 LaTeX 源文件和 PDF。
- `papers/`：轨道交通网络设计相关论文、文本摘录和阅读笔记。
- `doc/papers/`：文档目录下的论文材料副本。
