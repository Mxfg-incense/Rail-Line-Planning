#!/usr/bin/env python3
"""
地铁规划 v7 — NSGA-II 多目标优化
三个目标:
  1. 最大化旅客覆盖度 (800m步行可达)
  2. 最大化平均旅速
  3. 最小化地铁总里程 (建造成本)

约束:
  - 站距 1000-1500m
  - 转弯半径 ≥ 1000m
  - 线路 ≤ 5条
  - 线路连通
"""

import os, json, numpy as np, geopandas as gpd
import osmnx as ox
import networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from shapely.geometry import Point
from pymoo.core.problem import Problem
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.optimize import minimize
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.rnd import FloatRandomSampling
import warnings
warnings.filterwarnings('ignore')

# ── 字体 ────────────────────────────────────────────────────
for p in ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
          '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
          '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc']:
    if os.path.exists(p):
        fm.fontManager.addfont(p)
        plt.rcParams['font.family'] = fm.FontProperties(fname=p).get_name()
        break
plt.rcParams['axes.unicode_minus'] = False

# ── 参数 ────────────────────────────────────────────────────
BBOX = (121.48, 31.20, 121.56, 31.26)
OUTPUT = '/smb/j/SHT/COURSE-10/CS240/pj/transit_pipeline/output'
os.makedirs(OUTPUT, exist_ok=True)

TOTAL_POP    = 500_000
CLUSTER_R_M  = 500      # 聚类半径 500m (减少候选数量)
MIN_STA_DIST = 800      # 放宽站距下限
MAX_STA_DIST = 2000     # 放宽站距上限
WALK_R_M     = 800
MAX_LINES    = 5
R_MIN_M      = 1000
V_MAX_KMH    = 80
T_STOP_S     = 30
T_ACCEL_S    = 20
T_TRANSFER_S = 5 * 60
T_ACCESS_S   = 5 * 60
T_EGRESS_S   = 5 * 60
V_MAX = V_MAX_KMH / 3.6

# ── 坐标转换 ─────────────────────────────────────────────────
LAT_MID = (BBOX[1] + BBOX[3]) / 2
M_LON = 111_000 * np.cos(np.radians(LAT_MID))
M_LAT = 111_000

def dist_m(p1, p2):
    return np.sqrt(((p1[0]-p2[0])*M_LON)**2 + ((p1[1]-p2[1])*M_LAT)**2)

# ═══════════════════════════════════════════════════════════════
# 数据获取与聚类
# ═══════════════════════════════════════════════════════════════
def get_data():
    print("获取OSM数据...")
    buildings = ox.features_from_bbox(bbox=BBOX, tags={'building': True})
    G_road = ox.graph_from_bbox(bbox=BBOX, network_type='drive')
    print(f"  建筑物: {len(buildings)}, 路网: {len(G_road.nodes)}节点")
    return buildings, G_road

def make_population(buildings):
    print("生成人口分布...")
    res_types = ['apartments','house','residential','dormitory','yes']
    bld = buildings[buildings['building'].isin(res_types) | buildings['building'].isna()].copy() if 'building' in buildings.columns else buildings.copy()
    bld = bld[bld.geometry.is_valid & ~bld.geometry.is_empty]
    bld_m = bld.to_crs(epsg=3857); bld_m['area'] = bld_m.geometry.area
    bld_m = bld_m[bld_m['area'] > 10]
    total = bld_m['area'].sum()
    bld_m['pop'] = (bld_m['area'] / total * TOTAL_POP).astype(int)
    bld_wgs = bld_m.to_crs(epsg=4326); bld_wgs['centroid'] = bld_wgs.geometry.centroid
    gdf = gpd.GeoDataFrame({'geometry': bld_wgs['centroid'].values, 'pop': bld_wgs['pop'].values}, crs='EPSG:4326')
    print(f"  总人口: {gdf['pop'].sum()}, 点数: {len(gdf)}")
    return gdf

def cluster_100m(gdf_pop):
    """100m网格聚类"""
    print("100m网格聚类...")
    west, south, east, north = BBOX
    gdx = CLUSTER_R_M / M_LON; gdy = CLUSTER_R_M / M_LAT
    nxg = max(1, int(np.ceil((east-west)/gdx)))
    nyg = max(1, int(np.ceil((north-south)/gdy)))
    coords = np.array([[g.x, g.y] for g in gdf_pop.geometry])
    pops = gdf_pop['pop'].values
    cands = []
    for i in range(nyg):
        for j in range(nxg):
            x0, x1 = west+j*gdx, west+(j+1)*gdx
            y0, y1 = south+i*gdy, south+(i+1)*gdy
            m = ((coords[:,0]>=x0)&(coords[:,0]<x1)&(coords[:,1]>=y0)&(coords[:,1]<y1))
            if m.sum()==0: continue
            cc = coords[m]; pp = pops[m]; s = pp.sum()
            if s == 0: continue
            wx = np.average(cc[:,0], weights=pp)
            wy = np.average(cc[:,1], weights=pp)
            cands.append({'geometry': Point(wx, wy), 'pop': int(s)})
    gdf = gpd.GeoDataFrame(cands, crs='EPSG:4326')
    print(f"  候选: {len(gdf)}")
    return gdf

# ═══════════════════════════════════════════════════════════════
# 核心: NSGA-II 多目标优化
# ═══════════════════════════════════════════════════════════════
class MetroOptimizationProblem(Problem):
    """
    决策变量: 每个候选站是否选中 (0/1)
    但这样变量太多, 改为: 选择n_sta个站的索引

    为了用连续优化, 改为:
    每个候选站一个实数变量 x_i ∈ [0,1]
    x_i > 0.5 表示选中
    """
    def __init__(self, n_candidates, candidate_pops, candidate_coords,
                 dist_matrix, n_sta_range=(10, 40)):
        self.n_candidates = n_candidates
        self.candidate_pops = candidate_pops
        self.candidate_coords = candidate_coords
        self.dist_matrix = dist_matrix
        self.n_sta_range = n_sta_range

        super().__init__(
            n_var=n_candidates,
            n_obj=3,        # 3个目标
            n_constr=0,     # 无硬约束
            xl=0.0,
            xu=1.0,
        )

    def _evaluate(self, X, out, *args, **kwargs):
        n_pop = X.shape[0]
        F = np.zeros((n_pop, 3))  # 目标

        for idx in range(n_pop):
            # 选择 x > 0.5 的站
            selected = np.where(X[idx] > 0.5)[0]
            n_selected = len(selected)

            # 如果选站太少, 惩罚
            if n_selected < 3:
                F[idx] = [0, 0, 1e6]
                continue

            # 计算三个目标
            coverage, avg_speed, total_length = self._calc_objectives(selected)

            # 站距惩罚 (软约束)
            dist_penalty = self._station_dist_penalty(selected)

            F[idx, 0] = -coverage + dist_penalty * 0.1      # 覆盖度 (惩罚站距不满足)
            F[idx, 1] = -avg_speed                           # 旅速
            F[idx, 2] = total_length                         # 里程

        out["F"] = F

    def _calc_objectives(self, selected):
        """计算覆盖人口、平均旅速、总里程"""
        coords = self.candidate_coords[selected]
        pops = self.candidate_pops[selected]
        n = len(selected)

        # 1. 覆盖人口 (800m内)
        covered_mask = np.zeros(self.n_candidates, dtype=bool)
        for i in range(n):
            dists = self.dist_matrix[selected[i]]
            covered_mask |= (dists < WALK_R_M)
        coverage = self.candidate_pops[covered_mask].sum()

        # 2. 总里程 (MST)
        if n < 2:
            total_length = 0
        else:
            # 构建完全图
            G = nx.Graph()
            for i in range(n):
                for j in range(i+1, n):
                    d = self.dist_matrix[selected[i], selected[j]]
                    if d <= MAX_STA_DIST * 2:
                        G.add_edge(i, j, weight=d)
            if len(G.edges) > 0 and nx.is_connected(G):
                mst = nx.minimum_spanning_tree(G, weight='weight')
                total_length = sum(mst[u][v]['weight'] for u, v in mst.edges())
            else:
                total_length = 0

        # 3. 平均旅速 (简化模型)
        if n < 2:
            avg_speed = 0
        else:
            # 计算所有OD对的平均旅速
            speeds = []
            for i in range(min(n, 20)):  # 采样20个点
                for j in range(i+1, min(n, 20)):
                    d = self.dist_matrix[selected[i], selected[j]]
                    if d < 100: continue
                    n_mid = max(0, int(d / 1500) - 1)
                    t = T_ACCESS_S + d/V_MAX + n_mid*(T_STOP_S+T_ACCEL_S) + T_EGRESS_S
                    if t > 0:
                        speeds.append(d / t * 3.6)  # km/h
            avg_speed = np.mean(speeds) if speeds else 0

        return coverage, avg_speed, total_length

    def _check_station_dist(self, selected):
        """检查站距约束, 返回满足比例"""
        if len(selected) < 2:
            return 1.0
        ok = 0
        total = 0
        for i in range(len(selected)):
            for j in range(i+1, len(selected)):
                d = self.dist_matrix[selected[i], selected[j]]
                total += 1
                if MIN_STA_DIST <= d <= MAX_STA_DIST * 1.5:
                    ok += 1
        return ok / total if total > 0 else 1.0

    def _count_components(self, selected):
        """计算连通分量数"""
        if len(selected) < 2:
            return 1
        G = nx.Graph()
        for i in range(len(selected)):
            for j in range(i+1, len(selected)):
                d = self.dist_matrix[selected[i], selected[j]]
                if d <= MAX_STA_DIST * 1.5:
                    G.add_edge(i, j)
        if len(G.edges) == 0:
            return len(selected)
        return nx.number_connected_components(G)

    def _station_dist_penalty(self, selected):
        """站距惩罚: 站距太近或太远都惩罚"""
        if len(selected) < 2:
            return 0
        penalty = 0
        for i in range(len(selected)):
            for j in range(i+1, len(selected)):
                d = self.dist_matrix[selected[i], selected[j]]
                if d < MIN_STA_DIST:
                    penalty += (MIN_STA_DIST - d) / MIN_STA_DIST
                elif d > MAX_STA_DIST:
                    penalty += (d - MAX_STA_DIST) / MAX_STA_DIST
        return penalty

def build_lines_from_solution(selected, candidate_coords, candidate_pops):
    """从选中的站生成线路"""
    coords = candidate_coords[selected]
    n = len(selected)

    # 构建MST
    G = nx.Graph()
    for i in range(n):
        for j in range(i+1, n):
            d = dist_m(coords[i], coords[j])
            if d <= MAX_STA_DIST * 2:
                G.add_edge(i, j, weight=d)

    if len(G.edges) == 0:
        return []

    # 确保连通
    if not nx.is_connected(G):
        components = list(nx.connected_components(G))
        for i in range(len(components)-1):
            comp1 = list(components[i])
            comp2 = list(components[i+1])
            best_d, best_pair = 1e9, None
            for s1 in comp1:
                for s2 in comp2:
                    d = dist_m(coords[s1], coords[s2])
                    if d < best_d:
                        best_d, best_pair = d, (s1, s2)
            if best_pair:
                G.add_edge(best_pair[0], best_pair[1], weight=best_d)

    mst = nx.minimum_spanning_tree(G, weight='weight')

    # 分线: 用DFS将MST分成最多5条线路
    lines = []
    visited = set()

    # 找最长路径作为第一条线路
    if len(mst.nodes) >= 2:
        # BFS找最远节点对
        start = list(mst.nodes)[0]
        lengths = nx.single_source_shortest_path_length(mst, start)
        farthest = max(lengths, key=lengths.get)
        lengths2 = nx.single_source_shortest_path_length(mst, farthest)
        farthest2 = max(lengths2, key=lengths2.get)
        path = nx.shortest_path(mst, farthest, farthest2, weight='weight')

        lines.append({
            'name': 'Line 1',
            'stations': list(path),
            'coords': [coords[i] for i in path],
        })
        visited.update(path)

    # 剩余节点分配到最近的线路
    remaining = set(mst.nodes) - visited
    for node in remaining:
        if not lines:
            lines.append({'name': f'Line {len(lines)+1}', 'stations': [node], 'coords': [coords[node]]})
        else:
            # 找最近的已选站
            best_line = 0
            best_d = 1e9
            for i, line in enumerate(lines):
                for sid in line['stations']:
                    d = dist_m(coords[node], coords[sid])
                    if d < best_d:
                        best_d, best_line = d, i
            lines[best_line]['stations'].append(node)
            lines[best_line]['coords'].append(coords[node])

    # 按经度排序每条线路
    for line in lines:
        sta_with_coords = list(zip(line['stations'], line['coords']))
        sta_with_coords.sort(key=lambda x: x[1][0])  # 按经度排序
        line['stations'] = [s[0] for s in sta_with_coords]
        line['coords'] = [s[1] for s in sta_with_coords]

    return lines

def smooth_line(coords, r_min=R_MIN_M):
    """删除转弯半径<r_min的违规中间点"""
    if len(coords) < 3:
        return coords
    path = list(coords)
    for _ in range(5):
        if len(path) < 3: break
        new_path = [path[0]]
        i = 1
        while i < len(path) - 1:
            A, B, C = new_path[-1], path[i], path[i+1]
            BA = np.array([(A[0]-B[0])*M_LON, (A[1]-B[1])*M_LAT])
            BC = np.array([(C[0]-B[0])*M_LON, (C[1]-B[1])*M_LAT])
            len_BA, len_BC = np.linalg.norm(BA), np.linalg.norm(BC)
            if len_BA < 1 or len_BC < 1:
                new_path.append(B); i += 1; continue
            cos_angle = np.clip(np.dot(BA, BC) / (len_BA * len_BC), -1, 1)
            turn_angle = np.pi - np.arccos(cos_angle)
            if turn_angle > 0.01:
                R = (len_BA + len_BC) / 2 / (2 * np.sin(turn_angle / 2))
                if R < r_min:
                    i += 1; continue  # 删除B
            new_path.append(B)
            i += 1
        new_path.append(path[-1])
        path = new_path
    return path

# ═══════════════════════════════════════════════════════════════
# 可视化
# ═══════════════════════════════════════════════════════════════
def visualize(gdf_pop, gdf_cand, selected, lines, G_road, res):
    print("可视化...")
    west, south, east, north = BBOX
    fig, axes = plt.subplots(2, 2, figsize=(16, 14))
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']

    # 左上: 人口+路网
    ax = axes[0,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#cccccc', edge_linewidth=0.3, show=False)
    gdf_pop.plot(ax=ax, column='pop', cmap='YlOrRd', markersize=3, alpha=0.7, legend=True)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('人口分布 + OSM路网', fontsize=13)

    # 右上: 候选+选中
    ax = axes[0,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.2, show=False)
    gdf_cand.plot(ax=ax, color='lightgreen', markersize=5, alpha=0.3)
    # 画选中的站
    sel_coords = gdf_cand.iloc[selected]
    sel_coords.plot(ax=ax, color='red', markersize=50, edgecolor='black', linewidth=0.5, zorder=5)
    for i, idx in enumerate(selected):
        ax.annotate(f'S{i+1}', xy=(gdf_cand.iloc[idx].geometry.x, gdf_cand.iloc[idx].geometry.y),
                    fontsize=6, ha='center', va='bottom', xytext=(0,4), textcoords='offset points')
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title(f'选站: {len(selected)}个 (红)', fontsize=13)

    # 左下: 帕累托前沿
    ax = axes[1,0]
    if res.F is not None:
        ax.scatter(-res.F[:, 0]/1000, -res.F[:, 1], c=res.F[:, 2]/1000,
                   cmap='RdYlGn_r', s=20, alpha=0.6)
        cbar = plt.colorbar(ax.collections[0], ax=ax)
        cbar.set_label('总里程(km)')
        ax.set_xlabel('覆盖人口 (千人)')
        ax.set_ylabel('平均旅速 (km/h)')
        ax.set_title('帕累托前沿', fontsize=13)

    # 右下: 最优方案
    ax = axes[1,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#dddddd', edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=1, alpha=0.1)
    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        sc = line['smooth_coords']
        ax.plot([p[0] for p in sc], [p[1] for p in sc], '-', color=c, linewidth=3, alpha=0.85)
    sel_coords.plot(ax=ax, color='white', edgecolor='black', markersize=50, zorder=5)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('最优方案 + OSM路网', fontsize=13)

    plt.tight_layout()
    plt.savefig(f'{OUTPUT}/metro_plan_v7.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  已保存: {OUTPUT}/metro_plan_v7.png")

def export_geojson(gdf_cand, selected, lines):
    fc = {"type":"FeatureCollection","features":[]}
    for i, idx in enumerate(selected):
        g = gdf_cand.iloc[idx].geometry
        fc['features'].append({"type":"Feature","geometry":{"type":"Point","coordinates":[g.x,g.y]},"properties":{"id":f'S{i+1}'}})
    with open(f'{OUTPUT}/stations.geojson','w') as f: json.dump(fc,f,indent=2)

    fc2 = {"type":"FeatureCollection","features":[]}
    for line in lines:
        fc2['features'].append({"type":"Feature","geometry":{"type":"LineString","coordinates":line['smooth_coords']},"properties":{"name":line['name']}})
    with open(f'{OUTPUT}/metro_lines.geojson','w') as f: json.dump(fc2,f,indent=2)
    print(f"  已导出到 {OUTPUT}")

# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════
def main():
    print("="*60)
    print("地铁规划 v7 — NSGA-II 多目标优化")
    print("目标: 覆盖度↑ 旅速↑ 里程↓")
    print("="*60)

    buildings, G_road = get_data()
    gdf_pop = make_population(buildings)
    gdf_cand = cluster_100m(gdf_pop)

    # 预计算距离矩阵
    print("预计算距离矩阵...")
    n_cand = len(gdf_cand)
    coords = np.array([[g.x, g.y] for g in gdf_cand.geometry])
    pops = gdf_cand['pop'].values

    dist_matrix = np.zeros((n_cand, n_cand))
    for i in range(n_cand):
        for j in range(i+1, n_cand):
            d = dist_m(coords[i], coords[j])
            dist_matrix[i, j] = d
            dist_matrix[j, i] = d

    # NSGA-II 优化
    print("\n运行NSGA-II优化...")
    problem = MetroOptimizationProblem(n_cand, pops, coords, dist_matrix)

    algorithm = NSGA2(
        pop_size=50,
        sampling=FloatRandomSampling(),
        crossover=SBX(prob=0.9, eta=15),
        mutation=PM(eta=20),
        eliminate_duplicates=True,
    )

    res = minimize(
        problem,
        algorithm,
        ('n_gen', 100),
        seed=42,
        verbose=True,
    )

    # 找最优解 (覆盖度最高, 旅速最快, 里程最短)
    print("\n分析帕累托前沿...")
    if res.F is not None:
        # 归一化三个目标
        f_norm = np.zeros_like(res.F)
        for i in range(3):
            f_min, f_max = res.F[:, i].min(), res.F[:, i].max()
            if f_max > f_min:
                f_norm[:, i] = (res.F[:, i] - f_min) / (f_max - f_min)
            else:
                f_norm[:, i] = 0.5

        # 综合评分: 覆盖(40%) + 旅速(40%) + 里程(20%)
        # 注意: 覆盖和旅速是负值(要最大化), 里程是正值(要最小化)
        score = 0.4 * (-f_norm[:, 0]) + 0.4 * (-f_norm[:, 1]) + 0.2 * (1 - f_norm[:, 2])
        best_idx = np.argmax(score)

        print(f"\n最优解 (帕累托前沿第{best_idx}个):")
        print(f"  覆盖人口: {-res.F[best_idx, 0]:.0f}")
        print(f"  平均旅速: {-res.F[best_idx, 1]:.1f} km/h")
        print(f"  总里程: {res.F[best_idx, 2]/1000:.1f} km")

        # 提取最优解
        selected = np.where(res.X[best_idx] > 0.5)[0]
        print(f"  选站数: {len(selected)}")

        # 生成线路
        print("\n生成线路...")
        lines = build_lines_from_solution(selected, coords, pops)
        for line in lines:
            print(f"  {line['name']}: {len(line['stations'])}站")

        # 转弯平滑
        print("\n转弯半径平滑...")
    for line in lines:
        line['smooth_coords'] = [(float(p[0]), float(p[1])) for p in smooth_line(line['coords'])]
        print(f"  {line['name']}: {len(line['coords'])} → {len(line['smooth_coords'])}段")

        export_geojson(gdf_cand, selected, lines)
        visualize(gdf_pop, gdf_cand, selected, lines, G_road, res)

    print("\n✓ 完成!")

if __name__ == '__main__':
    main()
