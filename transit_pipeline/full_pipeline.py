#!/usr/bin/env python3
"""
城市交通网络自动规划全流程演示
技术路线: osmnx → WorldPop → NetworkX (斯坦纳树) → pymoo (NSGA-II) → GeoJSON输出

目标城市: 上海市中心区域（人民广场附近）
"""

import os
import json
import numpy as np
import pandas as pd
import geopandas as gpd
import osmnx as ox
import networkx as nx
import matplotlib.pyplot as plt
from shapely.geometry import Point, LineString, MultiPoint
from shapely.ops import nearest_points
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# 第一步: 使用 osmnx 获取 OSM 数据
# ============================================================
def step1_get_osm_data(place_name, network_type='walk'):
    """
    获取指定区域的道路网络数据
    """
    print("=" * 60)
    print("第一步: 获取OSM道路网络数据")
    print("=" * 60)
    
    # 获取道路网络
    print(f"正在获取 {place_name} 的道路网络...")
    G = ox.graph_from_place(place_name, network_type=network_type)
    
    # 获取区域边界
    print("正在获取区域边界...")
    gdf_boundary = ox.geocode_to_gdf(place_name)
    
    # 获取建筑物数据（用于人口估计）
    print("正在获取建筑物数据...")
    tags = {'building': True}
    gdf_buildings = ox.features_from_place(place_name, tags=tags)
    
    # 获取兴趣点（POI）
    print("正在获取兴趣点数据...")
    tags_poi = {'amenity': ['hospital', 'school', 'university', 'government']}
    try:
        gdf_poi = ox.features_from_place(place_name, tags=tags_poi)
    except:
        gdf_poi = gpd.GeoDataFrame()
    
    print(f"✓ 道路网络: {len(G.nodes)} 个节点, {len(G.edges)} 条边")
    print(f"✓ 建筑物: {len(gdf_buildings)} 个")
    print(f"✓ 兴趣点: {len(gdf_poi)} 个")
    
    return G, gdf_boundary, gdf_buildings, gdf_poi


# ============================================================
# 第二步: 生成人口分布数据（模拟WorldPop数据）
# ============================================================
def step2_generate_population_data(G, gdf_buildings, total_population=50000):
    """
    基于建筑物数据生成人口分布
    实际项目中应使用WorldPop真实数据
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
    
    # 计算建筑物面积
    gdf_residential = gdf_residential.to_crs(epsg=3857)  # 转换到投影坐标系计算面积
    gdf_residential['area'] = gdf_residential.geometry.area
    gdf_residential = gdf_residential.to_crs(epsg=4326)  # 转回WGS84
    
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
def step3_identify_key_nodes(G, gdf_population, gdf_poi, num_hubs=5):
    """
    识别关键交通节点（高人口密度区域 + POI）
    """
    print("\n" + "=" * 60)
    print("第三步: 识别关键交通节点")
    print("=" * 60)
    
    # 方法1: 基于人口密度的聚类
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
        # 如果没有人口数据，使用随机点
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
# 第四步: 使用斯坦纳树算法生成初始网络
# ============================================================
def step4_steiner_tree_network(G, hub_nodes):
    """
    使用斯坦纳树算法连接所有枢纽节点
    """
    print("\n" + "=" * 60)
    print("第四步: 斯坦纳树网络生成")
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
    
    # 使用NetworkX的斯坦纳树近似算法
    print("计算斯坦纳树...")
    terminal_nodes = set(hub_nodes)
    
    # 简化版斯坦纳树：使用最小生成树近似
    # 首先构建度量闭包
    steiner_edges = []
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
    
    print(f"✓ 斯坦纳树网络:")
    print(f"  - 节点数: {len(G_steiner.nodes)}")
    print(f"  - 边数: {len(G_steiner.edges)}")
    print(f"  - 总长度: {total_length:.0f} 米 ({total_length/1000:.2f} 公里)")
    
    return G_steiner, steiner_path_unique


# ============================================================
# 第五步: 使用NSGA-II优化车站位置
# ============================================================
def step5_nsga2_station_optimization(G, G_steiner, gdf_population, num_stations=10):
    """
    使用NSGA-II多目标优化确定最优车站位置
    目标1: 最大化覆盖人口
    目标2: 最小化车站数量
    目标3: 最小化平均步行距离
    """
    print("\n" + "=" * 60)
    print("第五步: NSGA-II多目标优化")
    print("=" * 60)
    
    from pymoo.core.problem import Problem
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.optimize import minimize
    from pymoo.operators.crossover.sbx import SBX
    from pymoo.operators.mutation.pm import PM
    from pymoo.operators.sampling.rnd import FloatRandomSampling
    from pymoo.core.repair import Repair
    
    # 获取斯坦纳树上的候选位置（每100米一个点）
    candidate_nodes = []
    for u, v in G_steiner.edges():
        if 'length' in G_steiner[u][v]:
            length = G_steiner[u][v].get('length', 100)
            num_points = max(1, int(length / 100))
            for i in range(num_points + 1):
                t = i / num_points
                # 插值点（简化：直接使用端点）
                if i == 0:
                    candidate_nodes.append(u)
                elif i == num_points:
                    candidate_nodes.append(v)
    
    candidate_nodes = list(set(candidate_nodes))
    
    if len(candidate_nodes) < num_stations:
        num_stations = len(candidate_nodes)
    
    print(f"候选位置数: {len(candidate_nodes)}")
    print(f"优化目标车站数: {num_stations}")
    
    # 计算每个候选位置的覆盖人口
    coverage_data = []
    for node in candidate_nodes:
        station_point = Point(G.nodes[node]['x'], G.nodes[node]['y'])
        
        # 计算500米范围内的覆盖人口
        covered_pop = 0
        total_distance = 0
        count = 0
        
        for _, pop_row in gdf_population.iterrows():
            dist = station_point.distance(pop_row.geometry) * 111000  # 近似转换为米
            if dist <= 500:  # 500米覆盖半径
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
    
    # 定义优化问题
    class StationOptimizationProblem(Problem):
        def __init__(self, n_stations, n_candidates, coverage_data):
            super().__init__(
                n_var=n_stations,
                n_obj=3,
                xl=0.0,
                xu=float(n_candidates - 1),
                vtype=int
            )
            self.n_stations = n_stations
            self.n_candidates = n_candidates
            self.coverage_data = coverage_data
        
        def _evaluate(self, X, out, *args, **kwargs):
            F = np.zeros((X.shape[0], 3))
            
            for i in range(X.shape[0]):
                # 选择的车站索引（去重）
                selected = list(set(X[i].astype(int)))
                selected = [s for s in selected if s < self.n_candidates]
                
                if len(selected) == 0:
                    F[i] = [0, self.n_stations, 500]
                    continue
                
                # 目标1: 最大化覆盖人口（取负值因为pymoo最小化）
                total_covered = sum(self.coverage_data[s]['covered_population'] for s in selected)
                F[i, 0] = -total_covered
                
                # 目标2: 车站数量（越少越好）
                F[i, 1] = len(selected)
                
                # 目标3: 平均步行距离
                distances = [self.coverage_data[s]['avg_distance'] for s in selected]
                F[i, 2] = np.mean(distances) if distances else 500
            
            out["F"] = F
    
    # 运行NSGA-II
    print("运行NSGA-II优化...")
    problem = StationOptimizationProblem(num_stations, len(candidate_nodes), coverage_data)
    
    algorithm = NSGA2(
        pop_size=50,
        sampling=FloatRandomSampling(),
        crossover=SBX(prob=0.9, eta=15),
        mutation=PM(eta=20),
        eliminate_duplicates=True
    )
    
    res = minimize(
        problem,
        algorithm,
        ('n_gen', 100),
        seed=42,
        verbose=False
    )
    
    # 提取最优解
    best_solution = res.X[0].astype(int)
    best_solution = list(set(best_solution))
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
    
    total_covered = gdf_stations['covered_population'].sum()
    total_pop = gdf_population['population'].sum()
    coverage_rate = total_covered / total_pop * 100 if total_pop > 0 else 0
    
    print(f"✓ NSGA-II优化结果:")
    print(f"  - 优化后车站数: {len(gdf_stations)}")
    print(f"  - 覆盖人口: {total_covered} / {total_pop} ({coverage_rate:.1f}%)")
    print(f"  - 平均步行距离: {gdf_stations['avg_distance'].mean():.0f} 米")
    
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
    
    with open(os.path.join(output_dir, 'stations.geojson'), 'w') as f:
        json.dump(stations_geojson, f, indent=2)
    
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
    
    with open(os.path.join(output_dir, 'network.geojson'), 'w') as f:
        json.dump(lines_geojson, f, indent=2)
    
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
    
    with open(os.path.join(output_dir, 'population.geojson'), 'w') as f:
        json.dump(population_geojson, f, indent=2)
    
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
    ax1.set_title('人口分布 (Population Distribution)', fontsize=12)
    ax1.set_xlabel('经度 (Longitude)')
    ax1.set_ylabel('纬度 (Latitude)')
    
    # 图2: 道路网络和枢纽节点
    ax2 = axes[0, 1]
    gdf_boundary.plot(ax=ax2, color='lightgray', edgecolor='black')
    ox.plot_graph(G, ax=ax2, node_size=0, edge_color='gray', edge_linewidth=0.5, show=False)
    gdf_hubs.plot(ax=ax2, color='red', markersize=100, marker='*', zorder=5)
    for _, hub in gdf_hubs.iterrows():
        ax2.annotate(hub['hub_id'], xy=(hub.geometry.x, hub.geometry.y),
                    fontsize=8, ha='center', va='bottom')
    ax2.set_title('道路网络与枢纽节点 (Road Network & Hubs)', fontsize=12)
    
    # 图3: 斯坦纳树网络
    ax3 = axes[1, 0]
    gdf_boundary.plot(ax=ax3, color='lightgray', edgecolor='black')
    
    # 绘制斯坦纳树边
    for u, v in G_steiner.edges():
        x = [G.nodes[u]['x'], G.nodes[v]['x']]
        y = [G.nodes[u]['y'], G.nodes[v]['y']]
        ax3.plot(x, y, 'b-', linewidth=2, alpha=0.7)
    
    gdf_hubs.plot(ax=ax3, color='red', markersize=100, marker='*', zorder=5)
    ax3.set_title('斯坦纳树网络 (Steiner Tree Network)', fontsize=12)
    ax3.set_xlabel('经度 (Longitude)')
    ax3.set_ylabel('纬度 (Latitude)')
    
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
                    xytext=(0, 10), textcoords='offset points')
    
    # 绘制覆盖范围
    for _, station in gdf_stations.iterrows():
        circle = plt.Circle((station.geometry.x, station.geometry.y), 
                           0.005, color='green', alpha=0.1)  # ~500m
        ax4.add_patch(circle)
    
    ax4.set_title('优化后车站布局 (Optimized Station Layout)', fontsize=12)
    ax4.set_xlabel('经度 (Longitude)')
    ax4.set_ylabel('纬度 (Latitude)')
    
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
    print("=" * 60)
    
    # 配置参数
    # 使用上海市黄浦区作为测试区域（包含人民广场、南京路等）
    PLACE_NAME = "Huangpu District, Shanghai, China"
    OUTPUT_DIR = "/smb/j/SHT/COURSE-10/CS240/pj/transit_pipeline/output"
    NUM_HUBS = 6  # 枢纽节点数量
    NUM_STATIONS = 10  # 优化后车站数量
    TOTAL_POPULATION = 200000  # 模拟总人口
    
    try:
        # 第一步: 获取OSM数据
        G, gdf_boundary, gdf_buildings, gdf_poi = step1_get_osm_data(PLACE_NAME)
        
        # 第二步: 生成人口分布
        gdf_population, gdf_residential = step2_generate_population_data(
            G, gdf_buildings, TOTAL_POPULATION
        )
        
        # 第三步: 识别关键节点
        gdf_hubs, hub_nodes = step3_identify_key_nodes(G, gdf_population, gdf_poi, NUM_HUBS)
        
        # 第四步: 斯坦纳树网络
        G_steiner, steiner_path = step4_steiner_tree_network(G, hub_nodes)
        
        # 第五步: NSGA-II优化
        gdf_stations, optimization_result = step5_nsga2_station_optimization(
            G, G_steiner, gdf_population, NUM_STATIONS
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
        print("\n生成的文件可用于:")
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
