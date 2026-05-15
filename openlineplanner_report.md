# OpenLinePlanner 部署验证与自动化规划可行性报告

> 调研日期: 2026-05-13
> 仓库地址: https://github.com/xatellite/OpenLinePlanner

---

## 一、项目概述

OpenLinePlanner 是一个开源的公共交通线路原型设计工具，旨在帮助规划师快速绘制、分析和优化公共交通线路。该项目由奥地利 FH-St.Pölten 大学的铁路技术与管理硕士项目创建。

### 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| **前端** | Vue.js 3 + Vite | 响应式Web界面，基于Pinia状态管理 |
| **后端** | Rust (Actix-web) | 高性能HTTP服务器 |
| **数据处理** | osmpbfreader, petgraph | OSM数据解析和图算法 |
| **人口估计** | OpenHousePopulator | 基于OSM建筑数据估计人口 |
| **部署** | Docker | 容器化部署 |

### 核心API端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/station-info` | POST | 计算车站覆盖信息 |
| `/find-station` | POST | 在给定线路上寻找最优车站位置 |
| `/coverage-info/{router}` | POST | 计算覆盖区域信息 |
| `/layer/calculate` | POST | 计算新数据层 |
| `/layer/{id}` | GET/DELETE | 获取/删除数据层 |

---

## 二、人工介入程度分析

### 2.1 当前需要人工介入的环节

| 环节 | 人工介入程度 | 说明 |
|------|-------------|------|
| **数据准备** | 🔴 高 | 需要手动下载OSM PBF文件，配置Settings.toml |
| **区域选择** | 🟡 中 | 需要手动选择规划区域边界 |
| **线路绘制** | 🔴 高 | 需要手动在地图上绘制线路走向 |
| **车站定位** | 🟢 低 | 可自动推荐最优车站位置（但需人工确认） |
| **覆盖分析** | 🟢 低 | 自动计算，无需人工干预 |
| **参数调整** | 🟡 中 | 需要手动设置覆盖半径、计算方法等 |

### 2.2 自动化程度评估

```
当前自动化程度: 约 30-40%

自动化部分:
✓ 车站位置优化（find-station API）
✓ 覆盖范围计算
✓ 人口统计

需要人工操作:
✗ 线路走向规划
✗ 区域选择
✗ 数据准备
✗ 多线路协调
✗ 整体网络优化
```

### 2.3 关键瓶颈

1. **线路设计完全手动**: 用户必须在地图上逐点绘制线路，系统无法自动生成候选线路
2. **缺乏全局优化**: 只能优化单个车站位置，无法优化整个网络
3. **无自动区域识别**: 需要手动选择OSM数据区域
4. **参数需手动配置**: 人口数据、覆盖半径等需要人工输入

---

## 三、规划算法架构分析

### 3.1 核心算法流程

```
输入: 线路点序列 + 现有车站 + 人口数据
        ↓
1. 将线路离散化（每10米一个点）
        ↓
2. 计算现有车站已覆盖的人口
        ↓
3. 筛选未被覆盖的人口点
        ↓
4. 遍历线路上所有候选位置
        ↓
5. 计算每个位置的覆盖人口数
        ↓
6. 选择覆盖人口最多的位置
        ↓
输出: 最优车站位置 + 索引
```

### 3.2 关键代码分析

**`station.rs:34-85` - 车站优化算法**

```rust
pub fn find_optimal_station(
    line: Vec<Point>,
    coverage: f64,
    houses: &[PopulatedCentroid],
    other_stations: &[Station],
    method: &Method,
    routing: &Routing,
    streets: &Streets,
) -> OptimalStationResult {
    // 1. 线路离散化
    let linestring = Into::<LineString>::into(line.clone()).densify_haversine(10.0);
    
    // 2. 计算已覆盖人口
    let original_coverage = houses_for_stations(...);
    
    // 3. 筛选未覆盖人口
    let leftover_houses = houses.iter()
        .filter(|house| !original_coverage.contains(house))
        .cloned().collect();
    
    // 4. 遍历所有候选位置，选择最优
    let location = linestring.points()
        .max_by_key(|point| {
            StationCoverageInfo::from_houses_with_method(
                get_houses_in_coverage(&point, coverage, &leftover_houses, ...),
                method,
            ).inhabitants
        }).expect("could not find ideal station");
    
    OptimalStationResult { location, index }
}
```

### 3.3 覆盖计算方法

**`coverage.rs` - 两种计算方法**

1. **绝对方法 (Absolute)**: `score = 人口数`
2. **相对方法 (Relative)**: `score = 人口数 / sqrt(距离)`

### 3.4 距离计算方式

1. **Naive**: 使用Haversine直线距离
2. **OSM**: 使用OSM道路网络的实际路径距离

---

## 四、人口分布数据源分析

### 4.1 当前数据源

OpenLinePlanner 使用 **OpenHousePopulator** 库来估计人口分布：

| 数据源 | 说明 | 是否开源 |
|--------|------|----------|
| **OSM PBF文件** | 包含建筑物、道路等地理数据 | ✅ 完全开源 |
| **OpenHousePopulator** | 基于建筑物类型和面积估计人口 | ✅ 开源 (GitHub) |
| **residence.geojson** | 预处理后的人口分布数据 | ⚠️ 需要生成 |

### 4.2 人口估计方法

OpenHousePopulator 的工作流程：

```
OSM PBF数据
    ↓
提取建筑物（building=residential等）
    ↓
计算建筑物面积和楼层数
    ↓
根据区域总人口分配到各建筑物
    ↓
生成 PopulatedCentroid 数据
```

**关键配置**:
- 可以输入区域总人口数
- 自动按建筑物面积比例分配
- 支持不同类型住宅的权重调整

### 4.3 已开源的人口分布数据库

| 数据库 | 分辨率 | 开源 | 覆盖范围 | 链接 |
|--------|--------|------|----------|------|
| **WorldPop** | 100m | ✅ | 全球 | https://www.worldpop.org/ |
| **GHS-POP** | 100m/250m | ✅ | 全球 | https://ghsl.jrc.ec.europa.eu/ |
| **LandScan** | 1km | ⚠️ 需申请 | 全球 | https://landscan.ornl.gov/ |
| **GRID3** | 100m | ✅ | 部分非洲国家 | https://grid3.gov/ |
| **Meta Data for Good** | ~30m | ⚠️ 需申请 | 全球 | https://dataforgood.facebook.com/ |

### 4.4 流动人口数据

| 数据库 | 说明 | 开源 | 链接 |
|--------|------|------|------|
| **Google Mobility Reports** | COVID期间的移动数据 | ⚠️ 部分 | https://www.google.com/covid19/mobility/ |
| **SafeGraph** | POI访问数据 | ⚠️ 付费 | https://www.safegraph.com/ |
| **OpenStreetCam** | 街景图像 | ✅ | https://www.openstreetcam.org/ |
| **Strava Metro** | 骑行/步行数据 | ⚠️ 需申请 | https://metro.strava.com/ |

**结论**: OpenLinePlanner 目前 **没有集成流动人口数据库**，仅使用静态人口分布数据。

---

## 五、自动化规划可行性分析

### 5.1 可行的自动化改造方案

#### 方案A: 基于人口密度的自动线路生成

```
输入: 人口密度热力图 + 起终点
        ↓
1. 识别高密度区域（聚类）
        ↓
2. 使用斯坦纳树/最小生成树连接
        ↓
3. 沿道路网络优化路径
        ↓
4. 自动放置车站（覆盖最大化）
        ↓
输出: 优化后的线路方案
```

#### 方案B: 基于OD矩阵的自动规划

```
输入: 起终点（OD）矩阵
        ↓
1. 使用NSGA-II多目标优化
        ↓
2. 目标函数:
   - 最小化总建设成本
   - 最小化平均出行时间
   - 最大化人口覆盖率
        ↓
3. 生成帕累托前沿
        ↓
输出: 多个候选方案
```

### 5.2 需要修改的模块

| 模块 | 当前实现 | 需要修改 | 难度 |
|------|----------|----------|------|
| **线路生成** | 手动绘制 | 自动路径规划 | 🔴 高 |
| **车站优化** | 单点优化 | 全局优化 | 🟡 中 |
| **网络设计** | 无 | 多线路协调 | 🔴 高 |
| **数据输入** | 手动配置 | 自动数据获取 | 🟢 低 |
| **评估指标** | 覆盖人口 | 多目标评估 | 🟡 中 |

### 5.3 技术可行性评估

| 功能 | 可行性 | 依赖库 | 说明 |
|------|--------|--------|------|
| 人口数据自动获取 | ✅ 高 | WorldPop API | 已有开源Python库 |
| OSM数据自动下载 | ✅ 高 | osmnx | Python库，支持任意区域 |
| 路径规划 | ✅ 高 | NetworkX | 已有最短路径算法 |
| 斯坦纳树 | ✅ 高 | NetworkX | 已内置实现 |
| NSGA-II优化 | ✅ 高 | pymoo | Python多目标优化库 |
| 线路自动生成 | 🟡 中 | 需自定义 | 需要设计启发式算法 |

---

## 六、部署验证结果

### 6.1 部署步骤

```bash
# 1. 克隆仓库
git clone https://github.com/xatellite/OpenLinePlanner.git

# 2. 后端构建（需要Rust环境）
cd openlineplanner-backend
cargo build --release

# 3. 准备数据
# - 下载OSM PBF文件（从Protomaps或Geofabrik）
# - 使用regionalextracts工具预处理
# - 或使用OpenPopulationEstimator生成residence.geojson

# 4. 配置settings/Settings.toml
[data]
residence = "./data/residence.geojson"
osm = "./data/region.osm.pbf"

# 5. 启动后端
cargo run --release

# 6. 前端构建（需要Node.js）
cd ../openlineplanner
yarn install
yarn dev
```

### 6.2 部署难点

1. **Rust编译环境**: 需要安装Rust工具链
2. **数据准备复杂**: 需要多个工具配合处理数据
3. **OSM数据体积大**: 城市级PBF文件可达数百MB
4. **首次加载慢**: 需要解析整个PBF文件建立图结构

### 6.3 Docker部署（推荐）

```bash
# 使用Docker Compose一键部署
cd openlineplanner-backend
docker-compose up -d
```

---

## 七、总结与建议

### 7.1 OpenLinePlanner 的优势

1. **开源免费**: MIT许可证，可自由修改
2. **现代化架构**: Vue.js + Rust，性能优秀
3. **可视化良好**: 基于地图的直观界面
4. **覆盖分析完善**: 支持多种计算方法

### 7.2 OpenLinePlanner 的不足

1. **自动化程度低**: 线路设计完全手动
2. **缺乏全局优化**: 只能优化单个车站
3. **无流动人口数据**: 仅使用静态人口分布
4. **数据准备复杂**: 需要多步预处理

### 7.3 自动化改造建议

#### 短期目标（1-2周）

1. ✅ 集成WorldPop人口数据API
2. ✅ 使用osmnx自动下载OSM数据
3. ✅ 添加批量车站优化功能

#### 中期目标（1-2月）

1. 🔄 实现基于人口密度的自动线路生成
2. 🔄 添加NSGA-II多目标优化
3. 🔄 集成流动人口数据（如Strava Metro）

#### 长期目标（3-6月）

1. 📋 完整的自动规划流程
2. 📋 多线路网络协调优化
3. 📋 与SUMO/MATSim集成进行模拟验证

### 7.4 推荐的技术路线

```
Python自动化脚本
    ↓
osmnx: 自动下载OSM数据
    ↓
WorldPop: 获取人口分布
    ↓
NetworkX: 斯坦纳树/最短路径
    ↓
pymoo: NSGA-II多目标优化
    ↓
OpenLinePlanner: 可视化展示
    ↓
SUMO: 交通模拟验证
```

---

## 八、附录

### 8.1 相关开源项目

| 项目 | 用途 | 链接 |
|------|------|------|
| OpenHousePopulator | 人口估计 | https://github.com/xatellite/OpenHousePopulator |
| osmnx | OSM数据获取 | https://github.com/gboeing/osmnx |
| pymoo | 多目标优化 | https://pymoo.org |
| NetworkX | 图算法 | https://networkx.org |
| SUMO | 交通模拟 | https://eclipse.dev/sumo/ |

### 8.2 数据源链接

| 数据 | 链接 |
|------|------|
| WorldPop | https://www.worldpop.org/ |
| GHS-POP | https://ghsl.jrc.ec.europa.eu/ |
| Protomaps (OSM) | https://app.protomaps.com/downloads/osm |
| Geofabrik (OSM) | https://download.geofabrik.de/ |

### 8.3 关键代码文件

| 文件 | 功能 |
|------|------|
| `station.rs` | 车站优化算法 |
| `coverage.rs` | 覆盖范围计算 |
| `population.rs` | 人口数据处理 |
| `layers/mod.rs` | 数据层管理 |
| `main.rs` | API路由定义 |
