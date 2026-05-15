#!/usr/bin/env python3
"""
地铁规划 v8 — 摒弃聚类, 用最大覆盖位置问题(MCLP)
核心思想:
1. 不聚类, 直接在所有建筑质心上选站
2. 每个候选点覆盖800m内人口
3. 用贪心MCLP选K个站, 最大化总覆盖
4. 线路用MST连接, 转弯半径约束
"""

import os, json, numpy as np, geopandas as gpd
import osmnx as ox
import networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from shapely.geometry import Point, LineString
from shapely.strtree import STRtree
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
WALK_R_M     = 800       # 步行可达半径
MIN_STA_DIST = 1000      # 最小站距
MAX_STA_DIST = 2000      # 最大站距
TARGET_STA   = 30        # 目标车站数
R_MIN_M      = 1000      # 转弯半径

# ── 坐标转换 ─────────────────────────────────────────────────
LAT_MID = (BBOX[1] + BBOX[3]) / 2
M_LON = 111_000 * np.cos(np.radians(LAT_MID))
M_LAT = 111_000

def dist_m(p1, p2):
    return np.sqrt(((p1[0]-p2[0])*M_LON)**2 + ((p1[1]-p2[1])*M_LAT)**2)

# ═══════════════════════════════════════════════════════════════
# 第1步: 获取数据
# ═══════════════════════════════════════════════════════════════
def get_data():
    print("第1步: 获取OSM数据...")
    buildings = ox.features_from_bbox(bbox=BBOX, tags={'building': True})
    G_road = ox.graph_from_bbox(bbox=BBOX, network_type='drive')
    print(f"  建筑物: {len(buildings)}, 路网: {len(G_road.nodes)}节点")
    return buildings, G_road

# ═══════════════════════════════════════════════════════════════
# 第2步: 人口分布 (每个建筑质心)
# ═══════════════════════════════════════════════════════════════
def make_population(buildings):
    print("第2步: 人口分布...")
    res_types = ['apartments','house','residential','dormitory','yes']
    bld = buildings[buildings['building'].isin(res_types) | buildings['building'].isna()].copy() if 'building' in buildings.columns else buildings.copy()
    bld = bld[bld.geometry.is_valid & ~bld.geometry.is_empty]
    bld_m = bld.to_crs(epsg=3857); bld_m['area'] = bld_m.geometry.area
    bld_m = bld_m[bld_m['area'] > 10]
    total = bld_m['area'].sum()
    bld_m['pop'] = (bld_m['area'] / total * TOTAL_POP).astype(int)
    bld_wgs = bld_m.to_crs(epsg=4326); bld_wgs['centroid'] = bld_wgs.geometry.centroid
    gdf = gpd.GeoDataFrame({'geometry': bld_wgs['centroid'].values, 'pop': bld_wgs['pop'].values}, crs='EPSG:4326')
    gdf = gdf[gdf['pop'] > 0].reset_index(drop=True)
    print(f"  总人口: {gdf['pop'].sum()}, 点数: {len(gdf)}")
    return gdf

# ═══════════════════════════════════════════════════════════════
# 第3步: 最大覆盖位置问题 (MCLP) 选站
# ═══════════════════════════════════════════════════════════════
def mclp_select(gdf_pop, n_sta=TARGET_STA, walk_r=WALK_R_M, min_dist=MIN_STA_DIST):
    """
    最大覆盖位置问题 (Maximum Covering Location Problem)
    贪心算法: 每次选能覆盖最多未覆盖人口的点, 且与已选点距离>=min_dist

    不做聚类, 直接在所有建筑质心上选
    """
    print(f"第3步: MCLP选站 (目标{n_sta}站, 步行{walk_r}m)...")
    coords = np.array([[g.x, g.y] for g in gdf_pop.geometry])
    pops = gdf_pop['pop'].values
    n = len(coords)

    # 预计算距离矩阵 (只计算walk_r内的邻居)
    print("  预计算覆盖关系...")
    cover_sets = []  # cover_sets[i] = 能被站i覆盖的人口索引集
    for i in range(n):
        dx = (coords[:, 0] - coords[i, 0]) * M_LON
        dy = (coords[:, 1] - coords[i, 1]) * M_LAT
        dists = np.sqrt(dx**2 + dy**2)
        cover_sets.append(set(np.where(dists < walk_r)[0]))

    # 贪心MCLP
    selected = []
    covered = set()  # 已覆盖的人口索引

    for step in range(n_sta):
        best_idx = -1
        best_gain = -1

        # 找能覆盖最多未覆盖人口的点
        for i in range(n):
            if i in selected:
                continue

            # 检查与已选点的距离
            too_close = False
            for s in selected:
                d = dist_m(coords[i], coords[s])
                if d < min_dist:
                    too_close = True
                    break
            if too_close:
                continue

            # 计算新增覆盖人口
            new_cover = cover_sets[i] - covered
            gain = sum(pops[j] for j in new_cover)

            if gain > best_gain:
                best_gain = gain
                best_idx = i

        if best_idx == -1:
            break

        selected.append(best_idx)
        covered |= cover_sets[best_idx]
        total_covered = sum(pops[j] for j in covered)
        print(f"    第{step+1}站: 候选{best_idx}, 新增覆盖{best_gain}人, 总覆盖{total_covered}")

    # 创建结果GeoDataFrame
    gdf_sta = gpd.GeoDataFrame([{
        'geometry': Point(coords[i][0], coords[i][1]),
        'station_id': f'S{idx+1}',
        'covered_pop': sum(pops[j] for j in cover_sets[i])
    } for idx, i in enumerate(selected)], crs='EPSG:4326')

    total_covered = sum(pops[j] for j in covered)
    print(f"  ✓ 选站: {len(gdf_sta)}")
    print(f"  ✓ 覆盖: {total_covered} / {pops.sum()} ({total_covered/pops.sum()*100:.1f}%)")

    return gdf_sta, selected, cover_sets

# ═══════════════════════════════════════════════════════════════
# 第4步: 生成地铁线路 (MST连接)
# ═══════════════════════════════════════════════════════════════
def build_lines(gdf_sta):
    """用MST连接车站, 分成多条线路"""
    print("第4步: 生成线路 (MST连接)...")
    coords = {s['station_id']: (s.geometry.x, s.geometry.y) for _, s in gdf_sta.iterrows()}
    sta_ids = list(coords.keys())
    n = len(sta_ids)

    # 构建完全图
    G_full = nx.Graph()
    for i in range(n):
        for j in range(i+1, n):
            d = dist_m(coords[sta_ids[i]], coords[sta_ids[j]])
            if d <= MAX_STA_DIST * 2:
                G_full.add_edge(sta_ids[i], sta_ids[j], weight=d)

    # 确保连通
    if not nx.is_connected(G_full):
        components = list(nx.connected_components(G_full))
        for i in range(len(components)-1):
            comp1, comp2 = list(components[i]), list(components[i+1])
            best_d, best_pair = 1e9, None
            for s1 in comp1:
                for s2 in comp2:
                    d = dist_m(coords[s1], coords[s2])
                    if d < best_d:
                        best_d, best_pair = d, (s1, s2)
            if best_pair:
                G_full.add_edge(best_pair[0], best_pair[1], weight=best_d)

    mst = nx.minimum_spanning_tree(G_full, weight='weight')
    total_length = sum(mst[u][v]['weight'] for u, v in mst.edges())
    print(f"  MST: {len(mst.nodes)}节点, {len(mst.edges)}边, 总长{total_length/1000:.1f}km")

    # 分成多条线路 (按连通分量或DFS)
    lines = []
    visited = set()

    # 找最长路径作为第一条线路
    if len(mst.nodes) >= 2:
        start = list(mst.nodes)[0]
        lengths = nx.single_source_shortest_path_length(mst, start)
        farthest = max(lengths, key=lengths.get)
        lengths2 = nx.single_source_shortest_path_length(mst, farthest)
        farthest2 = max(lengths2, key=lengths2.get)
        path = nx.shortest_path(mst, farthest, farthest2, weight='weight')
        lines.append({'name': 'Line 1', 'stations': list(path), 'coords': [coords[s] for s in path]})
        visited.update(path)

    # 剩余站分配到最近线路
    remaining = set(sta_ids) - visited
    for sid in remaining:
        if not lines:
            lines.append({'name': 'Line 1', 'stations': [sid], 'coords': [coords[sid]]})
        else:
            best_line, best_d = 0, 1e9
            for i, line in enumerate(lines):
                for s in line['stations']:
                    d = dist_m(coords[sid], coords[s])
                    if d < best_d:
                        best_d, best_line = d, i
            lines[best_line]['stations'].append(sid)
            lines[best_line]['coords'].append(coords[sid])

    # 按经度排序
    for line in lines:
        paired = list(zip(line['stations'], line['coords']))
        paired.sort(key=lambda x: x[1][0])
        line['stations'] = [p[0] for p in paired]
        line['coords'] = [p[1] for p in paired]

    print(f"  生成 {len(lines)} 条线路:")
    for line in lines:
        print(f"    {line['name']}: {len(line['stations'])}站")

    return lines

# ═══════════════════════════════════════════════════════════════
# 第5步: 转弯半径约束
# ═══════════════════════════════════════════════════════════════
def turn_radius(A, B, C):
    BA = np.array([(A[0]-B[0])*M_LON, (A[1]-B[1])*M_LAT])
    BC = np.array([(C[0]-B[0])*M_LON, (C[1]-B[1])*M_LAT])
    len_BA, len_BC = np.linalg.norm(BA), np.linalg.norm(BC)
    if len_BA < 1 or len_BC < 1: return float('inf')
    cos_angle = np.clip(np.dot(BA, BC) / (len_BA * len_BC), -1, 1)
    turn_angle = np.pi - np.arccos(cos_angle)
    if turn_angle < 0.01: return float('inf')
    return (len_BA + len_BC) / 2 / (2 * np.sin(turn_angle / 2))

def smooth_lines(lines):
    print("第5步: 转弯半径约束...")
    for line in lines:
        coords = line['coords']
        if len(coords) < 3:
            line['smooth_coords'] = [(float(p[0]), float(p[1])) for p in coords]
            continue
        path = list(coords)
        for _ in range(5):
            if len(path) < 3: break
            new_path = [path[0]]
            i = 1
            while i < len(path) - 1:
                A, B, C = new_path[-1], path[i], path[i+1]
                R = turn_radius(A, B, C)
                if R < R_MIN_M:
                    i += 1  # 删除B
                else:
                    new_path.append(B)
                    i += 1
            new_path.append(path[-1])
            path = new_path
        line['smooth_coords'] = [(float(p[0]), float(p[1])) for p in path]
        print(f"  {line['name']}: {len(coords)} → {len(line['smooth_coords'])}段")

# ═══════════════════════════════════════════════════════════════
# 第6步: 可视化
# ═══════════════════════════════════════════════════════════════
def visualize(gdf_pop, gdf_sta, lines, G_road):
    print("第6步: 可视化...")
    west, south, east, north = BBOX
    fig, axes = plt.subplots(2, 2, figsize=(16, 14))
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']

    # 左上: 人口分布
    ax = axes[0,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#cccccc', edge_linewidth=0.3, show=False)
    gdf_pop.plot(ax=ax, column='pop', cmap='YlOrRd', markersize=3, alpha=0.7, legend=True)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('人口分布 (每个建筑质心)', fontsize=13)

    # 右上: MCLP选站结果
    ax = axes[0,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=2, alpha=0.3)
    gdf_sta.plot(ax=ax, color='red', markersize=60, edgecolor='black', linewidth=0.8, zorder=5)
    for _, s in gdf_sta.iterrows():
        ax.annotate(s['station_id'], xy=(s.geometry.x, s.geometry.y), fontsize=7,
                    ha='center', va='bottom', xytext=(0,5), textcoords='offset points')
        circle = plt.Circle((s.geometry.x, s.geometry.y), WALK_R_M/111000, color='green', alpha=0.08)
        ax.add_patch(circle)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title(f'MCLP选站 {len(gdf_sta)}个 (绿圈=800m步行覆盖)', fontsize=13)

    # 左下: 线路拓扑
    ax = axes[1,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.15, show=False)
    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        sc = line['smooth_coords']
        ax.plot([p[0] for p in sc], [p[1] for p in sc], '-', color=c, linewidth=2.5, alpha=0.8, label=line['name'])
    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=50, zorder=5)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('MST线路 + 转弯半径约束', fontsize=13)
    ax.legend(fontsize=8)

    # 右下: 最终方案
    ax = axes[1,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#dddddd', edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=1, alpha=0.1)
    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        sc = line['smooth_coords']
        ax.plot([p[0] for p in sc], [p[1] for p in sc], '-', color=c, linewidth=3, alpha=0.85)
    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=60, zorder=5)
    for _, s in gdf_sta.iterrows():
        ax.annotate(s['station_id'], xy=(s.geometry.x, s.geometry.y), fontsize=8,
                    ha='center', va='bottom', xytext=(0,5), textcoords='offset points', fontweight='bold')
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('最终地铁方案 + OSM路网', fontsize=13)

    plt.tight_layout()
    plt.savefig(f'{OUTPUT}/metro_plan_v8.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  已保存: {OUTPUT}/metro_plan_v8.png")

def export_geojson(gdf_sta, lines):
    fc = {"type":"FeatureCollection","features":[]}
    for _, s in gdf_sta.iterrows():
        fc['features'].append({"type":"Feature","geometry":{"type":"Point","coordinates":[s.geometry.x,s.geometry.y]},"properties":{"id":s['station_id'],"covered_pop":int(s['covered_pop'])}})
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
    print("地铁规划 v8 — MCLP最大覆盖 (摒弃聚类)")
    print("="*60)

    buildings, G_road = get_data()
    gdf_pop = make_population(buildings)
    gdf_sta, selected, cover_sets = mclp_select(gdf_pop)
    lines = build_lines(gdf_sta)
    smooth_lines(lines)
    export_geojson(gdf_sta, lines)
    visualize(gdf_pop, gdf_sta, lines, G_road)

    print("\n✓ 完成!")

if __name__ == '__main__':
    main()
