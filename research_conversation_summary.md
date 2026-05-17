# CS240 Project Research Conversation Summary

本文档整理了围绕 CS240 课程项目进行的调研与讨论内容，覆盖课程要求、LLM Context Selection 备选题、轨道交通线位规划方向、相关论文、开源实现、黏菌模型、遗传算法、以及 Cities: Skylines 人口/交通模型对项目建模的启发。

## 1. 课程项目要求

课程主题为 **Algorithms in Modern ML and Data Applications**，要求项目展示经典算法如何解决现代问题。重点是理解算法与应用之间的联系，不要求提出新研究或达到 SOTA 性能。

关键时间节点：

- Project Proposal Submission: 5 月 17 日截止。
- Project Presentation: 第 16 周，具体日期待定。
- Final Report Submission: presentation 后提交。

团队要求：

- 每组最多 5 人。
- 选题可以来自课程 Appendix，也可以自拟题目，但自拟题需要批准。
- Proposal 通过 Gradescope 提交。
- 每组只需要提交一份，并在平台上选择队友。

项目阶段要求：

1. Background and Related Work
   - 简要介绍应用领域。
   - 总结相关算法形式化。
   - 讨论代表性论文。
   - 说明为什么问题在算法上有趣。
   - 预期 1-2 页，体现深入理解。

2. Representative Paper / Method Selection
   - 解释选定论文的问题形式化。
   - 详细描述核心算法。
   - 严格分析算法复杂度。
   - 明确要复现实验或结果。

3. Reproduction and Implementation
   - 正确实现选定算法。
   - 至少复现原论文的部分结果。
   - 解释复现偏差或失败原因。
   - 初步分析运行时间和可扩展性。

4. Experimental Evaluation
   - 测量和分析运行时间。
   - 评估算法随数据规模增长的表现。
   - 至少与一个 baseline 或 variant 比较。

5. Optional Extension
   - 算法优化或启发式改进。
   - 近似方法或更好的数据预处理。
   - 替代数据集或额外实验。

Proposal 需要包含：

- Topic Selection
- Problem Formulation
- Related Work
- Algorithm and Technical Plan
- Project Scope and Expected Goals
- Team Information

最终提交物：

- Experimental Report
- Source Code
- 运行复现实验的说明文档


## 2. 主方向：轨道交通线位规划

后续讨论聚焦于轨道交通线位规划项目。初始设想包括：

- 人口数据，例如高德 API 或其他人口 proxy。
- 岗位数据，通过用地性质、POI、办公/商业/产业设施推断。
- 出行需求，使用 OD 矩阵。
- 既有轨交线路作为基础网络。
- 考虑客流强度、换乘效率、轨交参数、建设成本等因素。
- 优先使用经典算法。

建议将题目收窄为：

> 在已有轨交网络基础上，规划一条新增线路或延伸线，使其在建设长度预算内最大化潜在客流覆盖，并改善 OD 出行效率。

这个问题可以建模为图优化问题：

- 城市网格或交通小区是节点。
- 道路、已有轨交走廊、可建设走廊是边。
- 边有建设成本。
- 节点有人口、岗位、POI、已有站点距离等属性。
- OD 矩阵表示区域间出行需求。

可对应的经典算法：

- Shortest Path / A*
- k-shortest paths
- Budgeted Maximum Coverage
- Facility Location / k-median / p-median
- Min-cost Max-flow
- All-or-nothing assignment
- Steiner Tree / Prize-Collecting Steiner Tree
- Greedy approximation
- Dynamic programming variants

推荐形式化：

```text
Input:
Existing metro network G0
Candidate stations V
Candidate edges E
OD demand matrix D
Population/job weights P
Construction budget B

Output:
A new transit line L

Objective:
Maximize OD demand capture, population/job coverage, and transfer benefit,
while controlling construction cost and redundancy with existing lines.
```

评分函数示例：

```text
Score(line) =
  alpha * covered_population
+ beta  * covered_jobs
+ gamma * served_OD_demand
+ delta * transfer_connectivity_gain
- lambda * construction_cost
- mu * redundancy_with_existing_lines
```

更算法化的题目：

> Transit Line Alignment Planning as a Budgeted Prize-Collecting Path Problem

理由：

- 轨交线位天然是一条 path。
- 节点可赋予 prize，例如人口、岗位、OD 需求、换乘收益。
- 边有 cost，例如距离、施工成本、穿越惩罚。
- 预算内找 prize 最大的路径是经典 NP-hard 组合优化问题。
- 可用 greedy、DP、local search、shortest-path heuristic、遗传算法等方法求解。

## 4. 中国城市轨交规划中的成熟方法

中国城市语境下，成熟工程流程通常不是直接全局优化，而是：

```text
需求预测
  -> 客流走廊识别
  -> 线网/线位方案生成
  -> 客流分配
  -> 多指标评价
  -> 迭代修正
```

常见方法包括：

- 四阶段法：出行生成、出行分布、方式划分、交通分配。
- 主客流走廊识别。
- 点线面要素层次分析法。
- 功能层次分析法。
- 逐线规划扩充法。
- 主客流方向线网规划法。

项目中可采用的中国城市实现路线：

```text
城市空间数据
  -> 人口/岗位/POI/用地强度栅格化
  -> OD 需求构造或输入
  -> 识别主客流走廊
  -> 生成候选线路/候选站点
  -> 基于已有轨交网络做客流分配
  -> 评价覆盖、客流强度、换乘效率、建设成本
```

算法化映射：

- OD 估计：Gravity Model、IPF、矩阵平衡。
- 客流走廊识别：OD desire line 聚类、最大流密度路径。
- 候选线路生成：Shortest Path、k-shortest paths、A*。
- 站点选择：Maximum Coverage、p-median、k-median。
- 线位选择：Prize-Collecting Path、Budgeted Maximum Coverage、Steiner Tree approximation。
- 客流分配：All-or-nothing assignment、Logit route choice。
- 换乘效率：带换乘惩罚的最短路、平均 OD travel cost、平均换乘次数。

工程软件参考：

- TransCAD
- VISUM
- CUBE / EMME
- ArcGIS / QGIS + Python
- Python + GeoPandas + NetworkX + OSMnx

数据建议：

- 人口：WorldPop、统计年鉴、居住 POI、住宅小区 POI、居住用地。
- 岗位：办公、商业、产业园、学校、医院、工业用地、夜间灯光 proxy。
- OD：真实 OD 如果可得；否则用人口-岗位 gravity model。
- 既有轨交：OSM、高德、城市地铁官网、Wikipedia 或开放地图。
- 候选走廊：OSM 道路网、网格邻接图、高德路径。

## 5. 轨交线位规划相关论文与工作

调研中找到的论文大致分为五类。

### 5.1 综述类

1. Guihaire & Hao, Transit network design and scheduling: A global review, 2008.

用途：

- 建立公共交通网络设计、线路规划、发车频率、调度等问题框架。
- 可作为 proposal related work 开头。

2. Transit Route Network Design Problem review.

用途：

- 说明 TNDP / TRNDP 是经典 NP-hard 交通网络优化问题。
- 常见方法包括启发式、遗传算法、禁忌搜索、模拟退火、多目标优化等。

3. Transit network design problem: a half century of methodological research, 2025.

用途：

- 现代综述，覆盖长期方法发展。

### 5.2 Rapid Transit Network Design

1. Rapid transit network design for optimal cost and origin-destination demand capture.

用途：

- 非常贴合本项目。
- 强调 OD demand capture，而不是简单覆盖站点周边人口。
- 关注成本、站点位置、线路形状。

2. Optimization methods for the planning of rapid transit.

用途：

- 讨论 alignments and stations 的优化规划。

3. Planning rapid transit networks.

用途：

- 支撑 multiobjective programming、network design、station location 的讨论。

### 5.3 站点选址与线位联合优化

1. GIS and genetic algorithm based integrated optimization for rail transit system planning.

用途：

- 与本项目的数据设想高度相似。
- 同时优化 station locations 和 line alignments。
- 使用 GIS 和 Genetic Algorithm。

2. Design of Urban Rail Transit Network Constrained by Urban Road Network, Trips and Land-Use Characteristics.

用途：

- 直接把 road network、trips、land-use characteristics 放进城市轨交网络设计。
- 支撑土地利用、OD、路网约束。

3. A framework for urban railway transit system planning with demographic distribution and travel efficiency.

用途：

- 支撑人口分布与出行效率之间的建模关系。

### 5.4 线路规划、客流分配与运营参数

1. A Local Line Optimization Model for Urban Rail Considering Passenger Flow Allocation.

用途：

- 关注既有轨交网络中的局部线路优化和 passenger flow allocation。
- 可借鉴 all-or-nothing assignment。

2. Integrated approach to network design and frequency setting problem in railway rapid transit systems.

用途：

- 将 network design 和 frequency setting 放在一起。
- 可作为进一步扩展，但短期项目不建议全部实现。

3. Integrated Railway Rapid Transit Network Design and Line Planning problem with maximum profit.

用途：

- 目标是最大化净收益，同时考虑客流分配、频率、车辆选择。
- 模型完整但偏大。

### 5.5 路径选择与换乘效率

1. Route choice modelling for an urban rail transit network: past, recent progress and future prospects.

用途：

- 支撑换乘效率、路径选择、广义出行成本建模。

2. A Novel Shortest Path Query Algorithm Based on Optimized Adaptive Topology Structure.

用途：

- 北京轨交网络上的最短路径查询。
- 强调换乘时间对路径搜索的影响。

推荐论文主线：

```text
Rapid Transit Network Design
+ OD Demand Capture
+ Budgeted Coverage / Prize-Collecting Path
+ Shortest Path Assignment
```

## 6. 黏菌模型与上海地铁相关调研

用户提到一篇论文或工作：

> 利用黏菌规划上海地铁模型，用麦片代表站，黏菌会规划出最高效率运输营养的线路。最后发现大体上符合上海现实大部分规划，包括已经规划的短期和远期线路，也包括目前还没有安排的线路。

调研结果：

- 找到了高度相似的经典论文，但原始案例是东京轨道交通，而不是上海。

最可能对应的经典论文：

```text
Atsushi Tero et al.
Rules for Biologically Inspired Adaptive Network Design
Science, 2010
DOI: 10.1126/science.1177894
```

该论文实验：

- 使用 Physarum polycephalum，多头绒泡菌/黏菌。
- 用 oat flakes 代表城市或站点。
- 黏菌形成连接这些点的输运管网。
- 最后网络与现实铁路系统相似。

未找到严格对应“上海地铁 + 麦片代表站点 + 预测短期/远期规划线路”的正式论文。可能是：

1. 对东京黏菌实验的中文科普转述。
2. 某个课程、展览、媒体项目把方法搬到上海。
3. 后续 Physarum 交通网络模型在中国网络上的应用，但不是上海地铁。

中国交通网络相关论文：

```text
Yuxin Liu, Chao Gao, Zili Zhang
Simulating Transport Networks With a Physarum Foraging Model
IEEE Access, 2019
DOI: 10.1109/ACCESS.2019.2899382
```

启发：

- 黏菌模型适合生成网络骨架、候选走廊。
- 不适合作为复杂约束下的最终优化器。
- 可翻译为自适应网络设计模型：高流量边强化，低流量边衰减。

## 7. GitHub 开源实现

调研到几类有用开源项目。

### 7.1 黏菌 / Physarum 模型

1. MoeBuTa/SlimeMould

- Python 模拟黏菌行为。
- 使用南京地铁几何数据。
- 包含 `nanjing_nodes.geojson`、`nanjing_edges.geojson`、`simulation.ipynb`。
- 对“中国城市轨交 + 黏菌模型”非常有参考价值。

2. fogleman/physarum

- 通用 Physarum 运输网络模拟。
- Go 实现。
- 更偏生成式/粒子仿真。

3. maajor/Physarealm

- Grasshopper/Rhino 插件。
- 偏建筑和城市设计空间生成。

### 7.2 上海地铁数据

1. Ariza-Sun/MetroFlow

- 上海地铁 2017 年 5-8 月、302 个站点的 city-scale metro flow dataset。
- 包含 OD 提取、进出站流量提取 notebook。
- 数据在 Figshare，DOI: 10.6084/m9.figshare.28844942。
- 可作为真实上海 OD/客流评估数据源。

### 7.3 交通网络设计与分配

1. RenatoArbex/TransitNetworkDesign

- 公共交通网络设计问题 TNDP 的 benchmark 数据集。
- 包含 Mandl、Mumford 等经典实例。
- 可用于先复现算法，再迁移到上海。

2. jdlph/Path4GMNS

- 交通网络最短路、User Equilibrium 分配、DTA、OD estimation 等。
- 适合做 OD 分配、客流强度、路径效率。

3. asu-trans-ai-lab/GTFS2GMNS

- 将 GTFS 转换为 GMNS 网络格式。
- 可与 Path4GMNS 联动。

### 7.4 空间网络与城市数据处理

1. OSMnx

- 从 OpenStreetMap 获取城市道路/步行网络。
- 可做最短路、网络指标、空间分析。

2. city2graph

- 将街道、交通、OD、POI 等地理数据转为图结构。

3. gtfspy

- GTFS 公共交通网络分析。
- 支持 accessibility、travel time、transfer 等指标。

推荐组合：

```text
MetroFlow
  -> 上海站点、OD、客流

NetworkX / OSMnx
  -> 候选站点图和既有轨交图

MoeBuTa/SlimeMould
  -> 黏菌/仿生走廊生成

经典图算法
  -> 最短路、k-shortest paths、最大覆盖、Prize-Collecting Path

Path4GMNS 或自写 assignment
  -> OD travel cost、换乘次数、客流强度评估
```

## 8. 黏菌、遗传算法与经典算法的定位

讨论中有评论指出：

> 黏菌收敛性比较好，用来做初始化、看走廊骨架比较直观，但是规模与约束一旦上去就会遇到各种问题。一般处理这种问题还是用遗传算法比较多。

判断：

- 这个评论准确。
- 黏菌模型适合生成直观网络骨架。
- 黏菌难以直接处理复杂工程约束，例如线路长度、曲线半径、站间距、穿越限制、换乘站约束、建设成本分区。
- 黏菌参数敏感，扩散、衰减、流量强化、阈值裁剪都会影响结果。
- 最终结果通常是骨架，不是可直接落地的线位方案。

建议定位：

```text
黏菌模型：初始化 / 候选走廊生成 / visualization
遗传算法：多目标组合优化
经典图算法：理论骨架与 baseline
```

遗传算法适合原因：

- 约束多。
- 目标多。
- 变量离散和连续混合。
- 可用 penalty function 处理不满足约束的方案。
- 可以生成一组 Pareto 方案，而不仅是单一解。

可能的染色体表示：

```text
[start station, intermediate corridor nodes..., end station]
```

或：

```text
candidate segment bitset: 010110101...
candidate station bitset: 101001...
```

适应度函数：

```text
fitness =
  alpha * OD demand served
+ beta  * population/job coverage
+ gamma * transfer efficiency improvement
- lambda * construction cost
- mu * redundancy with existing lines
- eta * infeasibility penalty
```

建议 pipeline：

1. 黏菌模型从人口/岗位/OD 热点生成城市客流走廊骨架。
2. 候选图构建，将走廊骨架、已有轨交、道路网、高需求 OD 点转成候选站点和候选边。
3. 经典 baseline：
   - 最高 OD 对最短路。
   - 最大覆盖贪心。
   - k-shortest paths 候选线。
   - Prize-Collecting Path 启发式。
4. 遗传算法主优化：
   - 染色体表示新增线路或候选线组合。
   - fitness 综合客流覆盖、换乘效率、建设成本、冗余惩罚。
   - mutation 做站点插入、删除、替换。
   - crossover 交换线路片段。
   - repair 保证线路连通和长度预算。
5. 评估：
   - 覆盖人口/岗位。
   - 捕获 OD 需求。
   - 新增换乘站数量与质量。
   - 平均 OD 最短广义时间下降。
   - 线路断面客流强度。
   - 算法运行时间和规模扩展。

为符合 CS240，建议不要只说“使用遗传算法规划地铁”，而应表述为：

> 使用经典图算法生成候选走廊，用遗传算法进行多目标组合优化，并与贪心最大覆盖、最短路、Steiner tree / Prize-Collecting Path 启发式进行比较。

## 9. Cities: Skylines 人口与交通模型参考

曾讨论是否可以参考《城市：天际线》的人口模型，以节省建立城市人口和出行模型的时间。

结论：

- 可以参考其结构，但不建议照搬动态参数。
- 适合借鉴其“人口-岗位-目的地-路径选择-网络负载”抽象。

### 9.1 一代 Cities: Skylines 交通与市民模型

参考文章：

- How Traffic Works in Cities: Skylines, GameDeveloper.

公开资料核心内容：

- 每个市民有独立属性：姓名、教育水平、住宅地址、工作/上学地点、幸福度、当前活动、健康、财富、家庭关系。
- 市民按生命周期行动：儿童上小学，青少年上高中，青年上大学或工作，成年人工作并组建家庭，老人退休但仍会消费和使用城市服务。
- 市民出行不是随机的，而是基于交通基础设施寻找目的地路径。
- 路网可抽象为 nodes + segments。
- 道路、步道、路口、公交/地铁线路都进入路径搜索。
- 市民会考虑是否有车、是否能步行、是否有公交/轨交能加速旅程。
- 路径搜索可能类似 A* 或 Dijkstra，但开发者没有公开确认。
- 目的地包括工作、学校、商业设施、餐馆、诊所、医院等。
- 交通仿真和市民管理是分开的：市民决定去哪，车辆系统沿路径移动。
- 游戏有计算上限，不是所有市民都同时真实出行。
- 车辆通常不会持续动态重规划路径，除非原路径失效。

对项目启发：

```text
居民区 -> 工作/服务区 -> OD 需求 -> 轨交/道路图最短路分配
```

### 9.2 Cities: Skylines II Citizen Simulation & Lifepath

参考文章：

- Paradox 官方：Cities: Skylines II Citizen Simulation & Lifepath.

公开资料核心内容：

- 每个市民有 Lifepath：出生或迁入、成长、上学、工作、休闲、生病、失业、搬家、变老、死亡。
- 市民幸福度由 Well-being 和 Health 组成，会影响行为和工作能力。
- Well-being 受基础设施影响：电力、清洁水、污水处理、垃圾、污染、犯罪率、邮政、互联网、住房是否合适、商品消费机会等。
- 家庭结构影响住房选择，家庭倾向大房子，单身和学生可住小公寓。
- 年龄影响出行偏好：
  - 青少年更看重便宜。
  - 成年人更看重时间，愿意为更快交通方式付费。
  - 老年人更看重舒适和离目的地近。
- 教育系统有五个等级：Uneducated、Poorly Educated、Educated、Well Educated、Highly Educated。
- 青少年和成年人会在继续上学和工作之间选择，会比较当前收入和未来教育带来的收入。
- 工作岗位来自商业、工业、办公和城市服务设施，不同岗位有不同教育要求。
- 公司经营好会扩招，经营差会缩减岗位。
- 市民休闲会比较目的地休闲收益和路径成本。

对项目启发：

```text
人口 P_i
岗位/吸引力 A_j
不同群体出行偏好 theta
路径广义成本 C_ij
OD_ij = P_i * A_j * exp(-beta * C_ij)
```

### 9.3 建议借鉴与不建议照搬

建议借鉴：

1. 居住-就业分离。
2. 广义出行成本。
3. 不同人群权重。
4. 目的地吸引力。
5. 路径分配。

不建议照搬：

- 出生、死亡。
- 家庭搬迁。
- 租金。
- 疾病。
- 公司扩招。
- 完整个体 agent-based simulation。

最稳模型：

```text
参考 Cities: Skylines 的 agent-based 思路，
但把个体市民聚合成交通小区，
用人口-岗位 gravity model 生成 OD，
再用最短路/网络分配评价轨交新增线路。
```

评价函数：

```text
城市网格/交通小区
  -> 人口 P_i
  -> 岗位/吸引力 A_j
  -> Gravity OD: D_ij
  -> 既有轨交最短路成本 C_ij
  -> 新增线路后成本 C'_ij
  -> 线路收益 = sum_ij D_ij * max(0, C_ij - C'_ij)
```

## 10. 推荐最终项目定位

建议项目最终定位为：

> 基于 OD 需求捕获与多目标优化的城市轨道交通新增线路规划。

更具体的英文题目：

> Algorithmic Planning of New Urban Rail Transit Alignments via Demand Capture and Budgeted Path Optimization

或：

> Transit Line Alignment Planning as a Budgeted Prize-Collecting Path Problem

推荐技术路线：

```text
Data preparation:
  Existing metro network
  Population grid
  Job / attraction grid
  OD matrix by gravity model or real data

Candidate generation:
  High-demand OD pairs
  k-shortest paths
  Physarum-inspired corridor skeleton, optional

Optimization:
  Greedy maximum coverage baseline
  Shortest-path baseline
  Prize-Collecting Path heuristic
  Genetic Algorithm for multi-objective optimization

Evaluation:
  Population/job coverage
  OD demand capture
  Travel cost reduction
  Transfer count and transfer quality
  Passenger flow intensity
  Runtime and scalability
```

CS240 叙事重点：

- 经典图算法用于候选线路生成。
- 最大覆盖 / Prize-Collecting Path 用于预算约束选线。
- 最短路 / 网络分配用于客流与换乘效率评估。
- 遗传算法用于处理复杂多目标约束。
- 黏菌模型作为直观初始化和可视化增强，而不是唯一优化器。

## 11. 可直接用于 Proposal 的问题表述草稿

```text
We study the algorithmic planning of a new urban rail transit alignment
on top of an existing metro network. Given spatial population distribution,
employment attraction, an OD demand matrix, existing rail lines, and a
candidate corridor graph, the goal is to select a new line under a length
or construction budget that maximizes demand capture and improves network
travel efficiency.

We formulate the problem as a budgeted prize-collecting path / demand
coverage problem on a graph. Candidate stations receive rewards based on
population coverage, job accessibility, OD demand served, and transfer
benefit, while candidate edges incur construction costs. We compare
classical baselines such as shortest path, greedy maximum coverage, and
k-shortest path candidate generation with a genetic algorithm designed
for multi-objective line alignment optimization. We evaluate each method
by population/job coverage, OD demand capture, average generalized travel
cost reduction, transfer efficiency, passenger flow intensity, and runtime
scalability.
```

## 12. 下一步建议

建议后续优先完成：

1. 确定 case study 城市，优先上海、深圳、杭州、成都、南京之一。
2. 确定数据来源：
   - 如果选上海，优先考虑 MetroFlow。
   - 如果选南京，可参考 MoeBuTa/SlimeMould 的南京地铁几何数据。
3. 写出 proposal 的 1-2 页版本。
4. 选定主算法组合：
   - k-shortest paths
   - greedy maximum coverage
   - Prize-Collecting Path heuristic
   - genetic algorithm
5. 确定 baseline：
   - 最高 OD 对最短路。
   - 人口覆盖贪心。
   - 官方规划线路方向作为参考。
6. 确定评价指标：
   - OD demand capture。
   - Average generalized travel cost reduction。
   - Transfer count reduction。
   - Population/job coverage。
   - Runtime and scalability。

