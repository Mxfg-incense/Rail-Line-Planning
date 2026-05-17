#!/usr/bin/env python3
"""
城市交通网络自动规划全流程演示
技术路线: osmnx → 人口估计 → NetworkX (斯坦纳树) → pymoo (NSGA-II) → GeoJSON输出

目标城市: 上海市浦东新区
约束条件:
  - 线路转弯半径 >= 1000m
  - 站间距 1km - 2km
"""

import os
import json
import numpy as np
import pandas as pd
import geopandas as gpd
import osmnx as ox
import networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from shapely.geometry import Point, LineString, MultiPoint
from shapely.ops import nearest_points
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# 中文字体配置
# ============================================================
def setup_chinese_font():
    """配置matplotlib中文字体"""
    # 尝试多种中文字体
    font_candidates = [
        'Noto Sans CJK SC',
        'Noto Serif CJK SC',
        'AR PL UMing CN',
        'Droid Sans Fallback',
        'SimHei',
        'Microsoft YaHei',
        'WenQuanYi Micro Hei',
    ]
    
    for font_name in font_candidates:
        try:
            font_path = matplotlib.font_manager.findfont(
                matplotlib.font_manager.FontProperties(family=font_name)
            )
            if font_path and 'fallback' not in font_path.lower():
                plt.rcParams['font.family'] = font_name
                plt.rcParams['axes.unicode_minus'] = False
                print(f"✓ 使用字体: {font_name}")
                return FontProperties(family=font_name)
        except:
            continue
    
    # 如果上述都失败，使用默认sans-serif
    plt.rcParams['font.sans-serif'] = ['Noto Sans CJK SC', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    print("✓ 使用默认中文字体配置")
    return FontProperties(family='Noto Sans CJK SC')

# 全局字体变量
FONT_PROP = setup_chinese_font()
FONT_TITLE = FontProperties(family=FONT_PROP.get_name(), size=14, weight='bold')
FONT_LABEL = FontProperties(family=FONT_PROP.get_name(), size=10)


# ============================================================
# 第一步: 使用 osmnx 获取 OSM 数据
# ============================================================
def step1_get_osm_data(bbox, network_type='drive'):
    """
    获取指定区域的道路网络数据
    使用drive类型以获取适合车辆行驶的道路
    
    bbox格式: (west, south, east, north) = (left, bottom, right, top)
    """
    print("=" * 60)
    print("第一步: 获取OSM道路网络数据")
    print("=" * 60)
    
    west, south, east, north = bbox
    print(f"区域范围: W={west}, S={south}, E={east}, N={north}")
    
    # 获取道路网络
    print("正在获取道路网络...")
    G = ox.graph_from_bbox(bbox=bbox, network_type=network_type)
    
    # 获取建筑物数据（用于人口估计）
    print("正在获取建筑物数据...")
    tags = {'building': True}
    gdf_buildings = ox.features_from_bbox(bbox=bbox, tags=tags)
    
    # 获取兴趣点（POI）
    print("正在获取兴趣点数据...")
    tags_poi = {'amenity': ['hospital', 'school', 'university', 'government', 'transport_station']}
    try:
        gdf_poi = ox.features_from_bbox(bbox=bbox, tags=tags_poi)
    except:
        gdf_poi = gpd.GeoDataFrame()
    
    # 创建边界GeoDataFrame
    from shapely.geometry import box
    gdf_boundary = gpd.GeoDataFrame(
        [{'geometry': box(west, south, east, north)}],
        crs='EPSG:4326'
    )
    
    # 为每条边添加转弯半径相关属性
    print("处理道路网络属性...")
    for u, v, k, data in G.edges(keys=True, data=True):
        # 确保有length属性
        if 'length' not in data:
            data['length'] = 100  # 默认100米
        
        # 确保有highway属性
        if 'highway' not in data:
            data['highway'] = 'residential'
    
    print(f"✓ 道路网络: {len(G.nodes)} 个节点, {len(G.edges)} 条边")
    print(f"✓ 建筑物: {len(gdf_buildings)} 个")
    print(f"✓ 兴趣点: {len(gdf_poi)} 个")
    
    return G, gdf_boundary, gdf_buildings, gdf_poi


# ============================================================
# 第二步: 生成人口分布数据
# ============================================================
def step2_generate_population_data(G, gdf_buildings, total_population=500000):
    """
    基于建筑物数据生成人口分布
    """
    print("\n" + "=" * 60)
    print("第二步: 生成人口分布数据")
    print("=" * 60)
    
    # 筛选住宅类建筑物
    residential_types = ['apartments', 'house', 'residential', 'dormitory', 'yes']
    if 'building' in gdf_buildings.columns:
        mask = gdf_buildings['building'].isin(residential_types) | gdf_buildings['building'].isna()
        gdf_residential = gdf_buildings[mask].copy()
    else:
        gdf_residential = gdf_buildings.copy()
    
    # 过滤掉无效几何
    gdf_residential = gdf_residential[gdf_residential.geometry.is_valid]
    gdf_residential = gdf_residential[~gdf_residential.geometry.is_empty]
    
    # 计算建筑物面积
    gdf_residential = gdf_residential.to_crs(epsg=3857)
    gdf_residential['area'] = gdf_residential.geometry.area
    gdf_residential = gdf_residential.to_crs(epsg=4326)
    
    # 过滤面积过小的建筑物
    gdf_residential = gdf_residential[gdf_residential['area'] > 10]
    
    # 按面积比例分配人口
    total_area = gdf_residential['area'].sum()
    if total_area > 0:
        gdf_residential['population'] = (
            gdf_residential['area'] / total_area * total_population
        ).astype(int)
    else:
        gdf_residential['population'] = 1
    
    # 获取建筑物质心作为人口点
    gdf_residential['centroid'] = gdf_residential.geometry.centroid
    
    # 创建人口点GeoDataFrame
    population_points = []
    for idx, row in gdf_residential.iterrows():
        if row['population'] > 0:
            population_points.append({
                'geometry': row['centroid'],
                'population': row['population'],
                'building_area': row['area']
            })
    
    gdf_population = gpd.GeoDataFrame(population_points, crs='EPSG:4326')
    
    print(f"✓ 住宅建筑物: {len(gdf_residential)} 个")
    print(f"✓ 总人口: {gdf_population['population'].sum()}")
    print(f"✓ 人口点: {len(gdf_population)} 个")
    
    return gdf_population, gdf_residential


# ============================================================
# 第三步: 识别关键节点（交通发生点）
# ============================================================
def step3_identify_key_nodes(G, gdf_population, gdf_poi, num_hubs=8):
    """
    识别关键交通节点（高人口密度区域 + POI）
    """
    print("\n" + "=" * 60)
    print("第三步: 识别关键交通节点")
    print("=" * 60)
    
    from sklearn.cluster import KMeans
    
    if len(gdf_population) > 0:
        coords = np.array([[p.y, p.x] for p in gdf_population.geometry])
        weights = gdf_population['population'].values
        
        # 加权K-Means聚类
        kmeans = KMeans(n_clusters=num_hubs, random_state=42, n_init=10)
        
        # 扩展坐标以考虑权重
        expanded_coords = np.repeat(coords, np.maximum(weights.astype(int), 1), axis=0)
        kmeans.fit(expanded_coords)
        
        hub_locations = kmeans.cluster_centers_
    else:
        nodes = list(G.nodes)
        hub_nodes = np.random.choice(nodes, num_hubs, replace=False)
        hub_locations = np.array([[G.nodes[n]['y'], G.nodes[n]['x']] for n in hub_nodes])
    
    # 将hub位置映射到最近的路网节点
    hub_nodes = []
    for loc in hub_locations:
        nearest_node = ox.distance.nearest_nodes(G, loc[1], loc[0])
        hub_nodes.append(nearest_node)
    
    # 创建hub GeoDataFrame
    hub_points = []
    for i, node in enumerate(hub_nodes):
        hub_points.append({
            'hub_id': f'Hub_{i+1}',
            'geometry': Point(G.nodes[node]['x'], G.nodes[node]['y']),
            'osm_node': node,
            'lat': G.nodes[node]['y'],
            'lon': G.nodes[node]['x']
        })
    
    gdf_hubs = gpd.GeoDataFrame(hub_points, crs='EPSG:4326')
    
    print(f"✓ 识别了 {num_hubs} 个关键枢纽节点:")
    for _, hub in gdf_hubs.iterrows():
        print(f"  - {hub['hub_id']}: ({hub['lat']:.4f}, {hub['lon']:.4f})")
    
    return gdf_hubs, hub_nodes


# ============================================================
# 第四步: 使用斯坦纳树算法生成初始网络（考虑转弯半径约束）
# ============================================================
def step4_steiner_tree_network(G, hub_nodes, min_turn_radius=1000):
    """
    使用斯坦纳树算法连接所有枢纽节点
    考虑转弯半径约束: 转弯半径 >= min_turn_radius (米)
    """
    print("\n" + "=" * 60)
    print("第四步: 斯坦纳树网络生成（考虑转弯半径约束）")
    print("=" * 60)
    
    # 计算所有枢纽节点之间的最短路径
    print("计算枢纽节点间的最短路径...")
    paths = {}
    path_lengths = {}
    
    for i, source in enumerate(hub_nodes):
        for j, target in enumerate(hub_nodes):
            if i < j:
                try:
                    path = nx.shortest_path(G, source, target, weight='length')
                    length = nx.shortest_path_length(G, source, target, weight='length')
                    paths[(source, target)] = path
                    path_lengths[(source, target)] = length
                except nx.NetworkXNoPath:
                    print(f"  警告: 节点 {source} 和 {target} 之间无路径")
    
    # 构建完全图（枢纽节点之间的距离）
    G_complete = nx.Graph()
    for (source, target), length in path_lengths.items():
        G_complete.add_edge(source, target, weight=length)
    
    # 使用最小生成树近似斯坦纳树
    print("计算斯坦纳树...")
    mst = nx.minimum_spanning_tree(G_complete, weight='weight')
    
    # 展开MST为实际路径
    steiner_path = []
    for edge in mst.edges():
        source, target = edge
        if (source, target) in paths:
            steiner_path.extend(paths[(source, target)])
        elif (target, source) in paths:
            steiner_path.extend(paths[(target, source)])
    
    # 去重并保持顺序
    steiner_path_unique = []
    seen = set()
    for node in steiner_path:
        if node not in seen:
            steiner_path_unique.append(node)
            seen.add(node)
    
    # 提取斯坦纳树的边
    steiner_edges = []
    for i in range(len(steiner_path_unique) - 1):
        steiner_edges.append((steiner_path_unique[i], steiner_path_unique[i+1]))
    
    # 创建斯坦纳树子图
    G_steiner = nx.Graph()
    for u, v in steiner_edges:
        if G.has_edge(u, v):
            edge_data = G[u][v][0] if 0 in G[u][v] else G[u][v]
            G_steiner.add_edge(u, v, **edge_data)
    
    total_length = sum(nx.get_edge_attributes(G_steiner, 'length', 0).values())
    
    # 检查转弯半径约束
    print(f"\n检查转弯半径约束 (>= {min_turn_radius}m)...")
    violations = check_turn_radius(G_steiner, min_turn_radius)
    if violations > 0:
        print(f"  ⚠ 发现 {violations} 处可能违反转弯半径约束的节点")
        print("  提示: 实际项目中需要对路径进行平滑处理")
    else:
        print(f"  ✓ 所有转弯满足半径约束")
    
    print(f"\n✓ 斯坦纳树网络:")
    print(f"  - 节点数: {len(G_steiner.nodes)}")
    print(f"  - 边数: {len(G_steiner.edges)}")
    print(f"  - 总长度: {total_length:.0f} 米 ({total_length/1000:.2f} 公里)")
    
    return G_steiner, steiner_path_unique


def check_turn_radius(G, min_radius=1000):
    """检查网络中的转弯半径是否满足约束"""
    violations = 0
    
    for node in G.nodes():
        neighbors = list(G.neighbors(node))
        if len(neighbors) >= 2:
            # 计算相邻边之间的角度
            for i in range(len(neighbors)):
                for j in range(i+1, len(neighbors)):
                    n1, n2 = neighbors[i], neighbors[j]
                    
                    # 获取节点坐标
                    p0 = np.array([G.nodes[node].get('x', 0), G.nodes[node].get('y', 0)])
                    p1 = np.array([G.nodes[n1].get('x', 0), G.nodes[n1].get('y', 0)])
                    p2 = np.array([G.nodes[n2].get('x', 0), G.nodes[n2].get('y', 0)])
                    
                    # 计算角度
                    v1 = p1 - p0
                    v2 = p2 - p0
                    
                    # 转换为米（近似）
                    v1_m = v1 * 111000 * np.cos(np.radians(p0[1]))
                    v2_m = v2 * 111000
                    
                    # 计算转弯半径（基于路径长度和角度）
                    angle = np.arccos(np.clip(np.dot(v1_m, v2_m) / (np.linalg.norm(v1_m) * np.linalg.norm(v2_m) + 1e-10), -1, 1))
                    angle_deg = np.degrees(angle)
                    
                    # 如果角度太尖锐（转弯太急），可能违反约束
                    if angle_deg < 150:  # 转弯角度小于150度
                        # 估算转弯半径
                        edge_len = min(np.linalg.norm(v1_m), np.linalg.norm(v2_m))
                        if edge_len < min_radius:
                            violations += 1
    
    return violations


# ============================================================
# 第五步: 使用NSGA-II优化车站位置（考虑站距约束）
# ============================================================
def step5_nsga2_station_optimization(G, G_steiner, gdf_population, 
                                      min_station_dist=1000, max_station_dist=2000,
                                      num_stations=15):
    """
    使用NSGA-II多目标优化确定最优车站位置
    
    约束条件:
    - 站间距: min_station_dist ~ max_station_dist (米)
    - 覆盖半径: 500米
    """
    print("\n" + "=" * 60)
    print("第五步: NSGA-II多目标优化（站距约束: 1km-2km）")
    print("=" * 60)
    
    from pymoo.core.problem import Problem
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.optimize import minimize
    from pymoo.operators.crossover.sbx import SBX
    from pymoo.operators.mutation.pm import PM
    from pymoo.operators.sampling.rnd import FloatRandomSampling
    
    # 获取斯坦纳树上的候选位置
    candidate_nodes = []
    for u, v in G_steiner.edges():
        length = G_steiner[u][v].get('length', 100)
        # 每200米一个候选点
        num_points = max(1, int(length / 200))
        for i in range(num_points + 1):
            if i == 0:
                candidate_nodes.append(u)
            elif i == num_points:
                candidate_nodes.append(v)
    
    candidate_nodes = list(set(candidate_nodes))
    
    if len(candidate_nodes) < num_stations:
        num_stations = len(candidate_nodes)
    
    print(f"候选位置数: {len(candidate_nodes)}")
    print(f"优化目标车站数: {num_stations}")
    print(f"站距约束: {min_station_dist}m - {max_station_dist}m")
    
    # 计算每个候选位置的覆盖人口
    print("计算候选位置覆盖人口...")
    coverage_data = []
    for node in candidate_nodes:
        station_point = Point(G.nodes[node]['x'], G.nodes[node]['y'])
        
        # 计算500米范围内的覆盖人口
        covered_pop = 0
        total_distance = 0
        count = 0
        
        for _, pop_row in gdf_population.iterrows():
            # 使用Haversine距离（米）
            dist = station_point.distance(pop_row.geometry) * 111000
            if dist <= 500:
                covered_pop += pop_row['population']
                total_distance += dist
                count += 1
        
        avg_distance = total_distance / count if count > 0 else 500
        
        coverage_data.append({
            'node': node,
            'lat': G.nodes[node]['y'],
            'lon': G.nodes[node]['x'],
            'covered_population': covered_pop,
            'avg_distance': avg_distance
        })
    
    df_coverage = pd.DataFrame(coverage_data)
    
    # 计算候选节点之间的距离矩阵（用于站距约束）
    print("计算距离矩阵...")
    n_candidates = len(candidate_nodes)
    dist_matrix = np.full((n_candidates, n_candidates), float('inf'))
    
    for i in range(n_candidates):
        for j in range(i+1, n_candidates):
            pi = Point(coverage_data[i]['lon'], coverage_data[i]['lat'])
            pj = Point(coverage_data[j]['lon'], coverage_data[j]['lat'])
            dist = pi.distance(pj) * 111000  # 转换为米
            dist_matrix[i][j] = dist
            dist_matrix[j][i] = dist
    
    # 定义优化问题
    class StationOptimizationProblem(Problem):
        def __init__(self, n_stations, n_candidates, coverage_data, dist_matrix,
                     min_dist, max_dist):
            super().__init__(
                n_var=n_stations,
                n_obj=3,  # 3个目标
                n_constr=1,  # 1个约束（站距）
                xl=0.0,
                xu=float(n_candidates - 1),
                vtype=int
            )
            self.n_stations = n_stations
            self.n_candidates = n_candidates
            self.coverage_data = coverage_data
            self.dist_matrix = dist_matrix
            self.min_dist = min_dist
            self.max_dist = max_dist
        
        def _evaluate(self, X, out, *args, **kwargs):
            F = np.zeros((X.shape[0], 3))
            G = np.zeros((X.shape[0], 1))
            
            for i in range(X.shape[0]):
                # 选择的车站索引（去重并排序）
                selected = sorted(list(set(X[i].astype(int))))
                selected = [s for s in selected if s < self.n_candidates]
                
                if len(selected) == 0:
                    F[i] = [0, self.n_stations, 500]
                    G[i] = 0
                    continue
                
                # 目标1: 最大化覆盖人口（取负值因为pymoo最小化）
                total_covered = sum(self.coverage_data[s]['covered_population'] for s in selected)
                F[i, 0] = -total_covered
                
                # 目标2: 车站数量（越少越好）
                F[i, 1] = len(selected)
                
                # 目标3: 平均步行距离
                distances = [self.coverage_data[s]['avg_distance'] for s in selected]
                F[i, 2] = np.mean(distances) if distances else 500
                
                # 约束: 站间距必须在范围内
                # 计算违反约束的程度
                constraint_violation = 0
                for j in range(len(selected)):
                    for k in range(j+1, len(selected)):
                        d = self.dist_matrix[selected[j]][selected[k]]
                        if d < self.min_dist:
                            constraint_violation += (self.min_dist - d) / self.min_dist
                        elif d > self.max_dist:
                            constraint_violation += (d - self.max_dist) / self.max_dist
                
                G[i, 0] = constraint_violation
            
            out["F"] = F
            out["G"] = G
    
    # 运行NSGA-II
    print("运行NSGA-II优化...")
    problem = StationOptimizationProblem(
        num_stations, len(candidate_nodes), coverage_data, dist_matrix,
        min_station_dist, max_station_dist
    )
    
    algorithm = NSGA2(
        pop_size=100,
        sampling=FloatRandomSampling(),
        crossover=SBX(prob=0.9, eta=15),
        mutation=PM(eta=20),
        eliminate_duplicates=True
    )
    
    res = minimize(
        problem,
        algorithm,
        ('n_gen', 200),
        seed=42,
        verbose=False
    )
    
    # 检查优化结果
    if res is None or res.X is None:
        print("优化失败，使用贪心算法作为备选...")
        # 贪心算法：选择覆盖人口最多的位置
        sorted_candidates = sorted(enumerate(coverage_data), 
                                   key=lambda x: x[1]['covered_population'], 
                                   reverse=True)
        best_solution = [idx for idx, _ in sorted_candidates[:num_stations]]
    else:
        # 提取最优解（选择满足约束的解）
        best_idx = 0
        best_violation = float('inf')
        
        for idx in range(len(res.F)):
            if res.G is not None and res.G[idx][0] < best_violation:
                best_violation = res.G[idx][0]
                best_idx = idx
        
        best_solution = res.X[best_idx].astype(int)
        best_solution = sorted(list(set(best_solution)))
        best_solution = [s for s in best_solution if s < len(candidate_nodes)]
    
    # 创建车站GeoDataFrame
    station_data = []
    for i, idx in enumerate(best_solution):
        station_data.append({
            'station_id': f'Station_{i+1}',
            'geometry': Point(coverage_data[idx]['lon'], coverage_data[idx]['lat']),
            'covered_population': coverage_data[idx]['covered_population'],
            'avg_distance': coverage_data[idx]['avg_distance']
        })
    
    gdf_stations = gpd.GeoDataFrame(station_data, crs='EPSG:4326')
    
    # 计算站间距统计
    if len(gdf_stations) > 1:
        station_dists = []
        for i in range(len(gdf_stations)):
            for j in range(i+1, len(gdf_stations)):
                d = gdf_stations.iloc[i].geometry.distance(gdf_stations.iloc[j].geometry) * 111000
                station_dists.append(d)
        
        avg_station_dist = np.mean(station_dists)
        min_station_dist_actual = np.min(station_dists)
        max_station_dist_actual = np.max(station_dists)
    else:
        avg_station_dist = 0
        min_station_dist_actual = 0
        max_station_dist_actual = 0
    
    total_covered = gdf_stations['covered_population'].sum()
    total_pop = gdf_population['population'].sum()
    coverage_rate = total_covered / total_pop * 100 if total_pop > 0 else 0
    
    print(f"\n✓ NSGA-II优化结果:")
    print(f"  - 优化后车站数: {len(gdf_stations)}")
    print(f"  - 覆盖人口: {total_covered} / {total_pop} ({coverage_rate:.1f}%)")
    print(f"  - 平均步行距离: {gdf_stations['avg_distance'].mean():.0f} 米")
    print(f"  - 站间距统计:")
    print(f"    * 平均: {avg_station_dist:.0f}米")
    print(f"    * 最小: {min_station_dist_actual:.0f}米")
    print(f"    * 最大: {max_station_dist_actual:.0f}米")
    
    return gdf_stations, res


# ============================================================
# 第六步: 生成OpenLinePlanner兼容的GeoJSON
# ============================================================
def step6_export_geojson(G_original, gdf_stations, G_steiner, gdf_population, output_dir):
    """
    导出为OpenLinePlanner兼容的GeoJSON格式
    """
    print("\n" + "=" * 60)
    print("第六步: 导出GeoJSON数据")
    print("=" * 60)
    
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. 导出车站数据
    stations_geojson = {
        "type": "FeatureCollection",
        "features": []
    }
    
    for _, station in gdf_stations.iterrows():
        stations_geojson['features'].append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [station.geometry.x, station.geometry.y]
            },
            "properties": {
                "id": station['station_id'],
                "covered_population": int(station['covered_population']),
                "avg_distance": float(station['avg_distance'])
            }
        })
    
    with open(os.path.join(output_dir, 'stations.geojson'), 'w', encoding='utf-8') as f:
        json.dump(stations_geojson, f, indent=2, ensure_ascii=False)
    
    # 2. 导出线路数据（斯坦纳树网络）
    lines_geojson = {
        "type": "FeatureCollection",
        "features": []
    }
    
    for u, v in G_steiner.edges():
        if 'geometry' in G_steiner[u][v]:
            geom = G_steiner[u][v]['geometry']
            coords = list(geom.coords)
        else:
            coords = [
                (G_original.nodes[u]['x'], G_original.nodes[u]['y']),
                (G_original.nodes[v]['x'], G_original.nodes[v]['y'])
            ]
        
        lines_geojson['features'].append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "properties": {
                "length": G_steiner[u][v].get('length', 0)
            }
        })
    
    with open(os.path.join(output_dir, 'network.geojson'), 'w', encoding='utf-8') as f:
        json.dump(lines_geojson, f, indent=2, ensure_ascii=False)
    
    # 3. 导出人口分布数据
    population_geojson = {
        "type": "FeatureCollection",
        "features": []
    }
    
    for _, pop in gdf_population.iterrows():
        population_geojson['features'].append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [pop.geometry.x, pop.geometry.y]
            },
            "properties": {
                "population": int(pop['population'])
            }
        })
    
    with open(os.path.join(output_dir, 'population.geojson'), 'w', encoding='utf-8') as f:
        json.dump(population_geojson, f, indent=2, ensure_ascii=False)
    
    print(f"✓ 已导出到 {output_dir}:")
    print(f"  - stations.geojson: 车站位置")
    print(f"  - network.geojson: 线路网络")
    print(f"  - population.geojson: 人口分布")
    
    return output_dir


# ============================================================
# 第七步: 可视化结果
# ============================================================
def step7_visualization(G, gdf_boundary, gdf_population, gdf_hubs, 
                       gdf_stations, G_steiner, output_dir):
    """
    可视化规划结果
    """
    print("\n" + "=" * 60)
    print("第七步: 生成可视化图表")
    print("=" * 60)
    
    fig, axes = plt.subplots(2, 2, figsize=(16, 14))
    
    # 图1: 人口分布热力图
    ax1 = axes[0, 0]
    gdf_boundary.plot(ax=ax1, color='lightgray', edgecolor='black')
    gdf_population.plot(ax=ax1, column='population', cmap='YlOrRd', 
                        markersize=5, alpha=0.6, legend=True)
    ax1.set_title('人口分布 (Population Distribution)', fontproperties=FONT_TITLE)
    ax1.set_xlabel('经度 (Longitude)', fontproperties=FONT_LABEL)
    ax1.set_ylabel('纬度 (Latitude)', fontproperties=FONT_LABEL)
    
    # 图2: 道路网络和枢纽节点
    ax2 = axes[0, 1]
    gdf_boundary.plot(ax=ax2, color='lightgray', edgecolor='black')
    ox.plot_graph(G, ax=ax2, node_size=0, edge_color='gray', edge_linewidth=0.5, show=False)
    gdf_hubs.plot(ax=ax2, color='red', markersize=100, marker='*', zorder=5)
    for _, hub in gdf_hubs.iterrows():
        ax2.annotate(hub['hub_id'], xy=(hub.geometry.x, hub.geometry.y),
                    fontsize=8, ha='center', va='bottom', fontproperties=FONT_LABEL)
    ax2.set_title('道路网络与枢纽节点 (Road Network & Hubs)', fontproperties=FONT_TITLE)
    
    # 图3: 斯坦纳树网络
    ax3 = axes[1, 0]
    gdf_boundary.plot(ax=ax3, color='lightgray', edgecolor='black')
    
    # 绘制斯坦纳树边
    for u, v in G_steiner.edges():
        x = [G.nodes[u]['x'], G.nodes[v]['x']]
        y = [G.nodes[u]['y'], G.nodes[v]['y']]
        ax3.plot(x, y, 'b-', linewidth=2, alpha=0.7)
    
    gdf_hubs.plot(ax=ax3, color='red', markersize=100, marker='*', zorder=5)
    ax3.set_title('斯坦纳树网络 (Steiner Tree Network)', fontproperties=FONT_TITLE)
    ax3.set_xlabel('经度 (Longitude)', fontproperties=FONT_LABEL)
    ax3.set_ylabel('纬度 (Latitude)', fontproperties=FONT_LABEL)
    
    # 图4: 最终车站布局
    ax4 = axes[1, 1]
    gdf_boundary.plot(ax=ax4, color='lightgray', edgecolor='black')
    gdf_population.plot(ax=ax4, column='population', cmap='YlOrRd', 
                        markersize=3, alpha=0.3)
    
    # 绘制网络
    for u, v in G_steiner.edges():
        x = [G.nodes[u]['x'], G.nodes[v]['x']]
        y = [G.nodes[u]['y'], G.nodes[v]['y']]
        ax4.plot(x, y, 'b-', linewidth=1.5, alpha=0.5)
    
    # 绘制车站
    gdf_stations.plot(ax=ax4, color='green', markersize=150, marker='^', 
                      edgecolor='black', zorder=5)
    for _, station in gdf_stations.iterrows():
        ax4.annotate(station['station_id'], 
                    xy=(station.geometry.x, station.geometry.y),
                    fontsize=8, ha='center', va='bottom',
                    xytext=(0, 10), textcoords='offset points',
                    fontproperties=FONT_LABEL)
    
    # 绘制覆盖范围（500米）
    for _, station in gdf_stations.iterrows():
        circle = plt.Circle((station.geometry.x, station.geometry.y), 
                           0.005, color='green', alpha=0.1)
        ax4.add_patch(circle)
    
    ax4.set_title('优化后车站布局 (Optimized Station Layout)', fontproperties=FONT_TITLE)
    ax4.set_xlabel('经度 (Longitude)', fontproperties=FONT_LABEL)
    ax4.set_ylabel('纬度 (Latitude)', fontproperties=FONT_LABEL)
    
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'transit_planning_result.png'), 
                dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"✓ 已保存可视化结果: {output_dir}/transit_planning_result.png")


# ============================================================
# 主函数
# ============================================================
def main():
    """
    主函数：运行完整的交通网络规划流程
    """
    print("=" * 60)
    print("城市交通网络自动规划全流程演示")
    print("目标: 上海市浦东新区")
    print("约束: 转弯半径>=1000m, 站距1km-2km")
    print("=" * 60)
    
    # 配置参数
    # 浦东新区陆家嘴-世纪公园区域 (bbox: west, south, east, north)
    BBOX = (121.48, 31.20, 121.56, 31.26)
    OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
    NUM_HUBS = 8  # 枢纽节点数量
    NUM_STATIONS = 15  # 目标车站数量
    TOTAL_POPULATION = 500000  # 模拟总人口
    MIN_STATION_DIST = 1000  # 最小站距（米）
    MAX_STATION_DIST = 2000  # 最大站距（米）
    MIN_TURN_RADIUS = 1000  # 最小转弯半径（米）
    
    try:
        # 第一步: 获取OSM数据
        G, gdf_boundary, gdf_buildings, gdf_poi = step1_get_osm_data(BBOX)
        
        # 第二步: 生成人口分布
        gdf_population, gdf_residential = step2_generate_population_data(
            G, gdf_buildings, TOTAL_POPULATION
        )
        
        # 第三步: 识别关键节点
        gdf_hubs, hub_nodes = step3_identify_key_nodes(G, gdf_population, gdf_poi, NUM_HUBS)
        
        # 第四步: 斯坦纳树网络（考虑转弯半径）
        G_steiner, steiner_path = step4_steiner_tree_network(G, hub_nodes, MIN_TURN_RADIUS)
        
        # 第五步: NSGA-II优化（考虑站距约束）
        gdf_stations, optimization_result = step5_nsga2_station_optimization(
            G, G_steiner, gdf_population, 
            MIN_STATION_DIST, MAX_STATION_DIST,
            NUM_STATIONS
        )
        
        # 第六步: 导出GeoJSON
        step6_export_geojson(G, gdf_stations, G_steiner, gdf_population, OUTPUT_DIR)
        
        # 第七步: 可视化
        step7_visualization(G, gdf_boundary, gdf_population, gdf_hubs, 
                          gdf_stations, G_steiner, OUTPUT_DIR)
        
        print("\n" + "=" * 60)
        print("✓ 全流程完成!")
        print("=" * 60)
        print(f"\n输出文件目录: {OUTPUT_DIR}")
        print("\n约束条件检查:")
        print(f"  - 转弯半径 >= {MIN_TURN_RADIUS}m: 已考虑")
        print(f"  - 站间距: {MIN_STATION_DIST}m - {MAX_STATION_DIST}m")
        print(f"\n生成的文件可用于:")
        print("1. 导入OpenLinePlanner进行可视化展示")
        print("2. 导入SUMO进行交通模拟验证")
        print("3. 作为进一步优化的基础数据")
        
        return {
            'graph': G,
            'population': gdf_population,
            'hubs': gdf_hubs,
            'steiner_network': G_steiner,
            'stations': gdf_stations,
            'output_dir': OUTPUT_DIR
        }
        
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    result = main()
