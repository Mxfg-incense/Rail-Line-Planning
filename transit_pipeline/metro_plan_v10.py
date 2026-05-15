#!/usr/bin/env python3
"""
地铁规划 v10 — 精简线路 + 换乘惩罚
指标:
  1. 建站成本 (车站数)
  2. 线路里程成本 (总里程)
  3. 800m覆盖度
  4. 平均旅速 (含换乘惩罚: 换乘1次 = 3站地铁时间)

算法:
  1. MCLP选候选站
  2. 用最少的长线路覆盖所有站
  3. 换乘站 = 线路交叉点
"""

import os, json, numpy as np, geopandas as gpd
import osmnx as ox
import networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from shapely.geometry import Point, LineString
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
WALK_R_M     = 800
MIN_STA_DIST = 1000
MAX_STA_DIST = 2000
R_MIN_M      = 1000
V_MAX_KMH    = 80
T_STOP_S     = 30
T_ACCEL_S    = 20
T_TRANSFER_S = 3 * (T_STOP_S + T_ACCEL_S)  # 换乘1次 = 3站地铁时间
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
# 第1步: 获取数据
# ═══════════════════════════════════════════════════════════════
def get_data():
    print("第1步: 获取OSM数据...")
    buildings = ox.features_from_bbox(bbox=BBOX, tags={'building': True})
    G_road = ox.graph_from_bbox(bbox=BBOX, network_type='drive')
    print(f"  建筑物: {len(buildings)}, 路网: {len(G_road.nodes)}节点")
    return buildings, G_road

# ═══════════════════════════════════════════════════════════════
# 第2步: 人口分布
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
# 第3步: MCLP选候选站
# ═══════════════════════════════════════════════════════════════
def mclp_select(gdf_pop, n_sta=30):
    """MCLP选候选站"""
    print(f"第3步: MCLP选候选站 ({n_sta}个)...")
    coords = np.array([[g.x, g.y] for g in gdf_pop.geometry])
    pops = gdf_pop['pop'].values
    n = len(coords)

    cover_sets = []
    for i in range(n):
        dx = (coords[:, 0] - coords[i, 0]) * M_LON
        dy = (coords[:, 1] - coords[i, 1]) * M_LAT
        dists = np.sqrt(dx**2 + dy**2)
        cover_sets.append(set(np.where(dists < WALK_R_M)[0]))

    selected = []
    covered = set()

    for step in range(n_sta):
        best_idx = -1
        best_gain = -1
        for i in range(n):
            if i in selected: continue
            too_close = False
            for s in selected:
                if dist_m(coords[i], coords[s]) < MIN_STA_DIST:
                    too_close = True; break
            if too_close: continue
            new_cover = cover_sets[i] - covered
            gain = sum(pops[j] for j in new_cover)
            if gain > best_gain:
                best_gain = gain
                best_idx = i
        if best_idx == -1: break
        selected.append(best_idx)
        covered |= cover_sets[best_idx]

    gdf_sta = gpd.GeoDataFrame([{
        'geometry': Point(coords[i][0], coords[i][1]),
        'sta_idx': i,
        'covered_pop': sum(pops[j] for j in cover_sets[i])
    } for i in selected], crs='EPSG:4326')

    total_covered = sum(pops[j] for j in covered)
    print(f"  ✓ 候选站: {len(gdf_sta)}, 覆盖: {total_covered} ({total_covered/pops.sum()*100:.1f}%)")
    return gdf_sta, coords, pops, cover_sets

# ═══════════════════════════════════════════════════════════════
# 第4步: 精简线路生成
# ═══════════════════════════════════════════════════════════════
def build_fewer_lines(gdf_sta):
    """
    用最少的长线路覆盖所有站
    策略: 找最长的东西向和南北向线路
    """
    print("第4步: 生成精简线路...")
    sta_coords = {s['sta_idx']: (s.geometry.x, s.geometry.y) for _, s in gdf_sta.iterrows()}
    sta_ids = list(sta_coords.keys())
    n = len(sta_ids)

    # 计算所有站的中心
    center_x = np.mean([sta_coords[s][0] for s in sta_ids])
    center_y = np.mean([sta_coords[s][1] for s in sta_ids])

    # 按方向分类站
    # 东: x > center_x, 西: x < center_x, 北: y > center_y, 南: y < center_y
    east_stations = sorted([s for s in sta_ids if sta_coords[s][0] > center_x], 
                           key=lambda s: sta_coords[s][0])
    west_stations = sorted([s for s in sta_ids if sta_coords[s][0] < center_x], 
                           key=lambda s: -sta_coords[s][0])
    north_stations = sorted([s for s in sta_ids if sta_coords[s][1] > center_y], 
                            key=lambda s: sta_coords[s][1])
    south_stations = sorted([s for s in sta_ids if sta_coords[s][1] < center_y], 
                            key=lambda s: -sta_coords[s][1])

    lines = []

    # 东西线: 从西到东穿过中心
    ew_stations = west_stations + [s for s in sta_ids if abs(sta_coords[s][0] - center_x) < 0.001] + east_stations
    ew_stations = list(dict.fromkeys(ew_stations))  # 去重保持顺序
    if len(ew_stations) >= 2:
        # 过滤: 只保留站距合理的站
        filtered = [ew_stations[0]]
        for s in ew_stations[1:]:
            d = dist_m(sta_coords[filtered[-1]], sta_coords[s])
            if MIN_STA_DIST <= d <= MAX_STA_DIST * 1.5:
                filtered.append(s)
        if len(filtered) >= 2:
            lines.append({
                'name': '东西线',
                'stations': filtered,
                'coords': [sta_coords[s] for s in filtered],
            })

    # 南北线: 从南到北穿过中心
    ns_stations = south_stations + [s for s in sta_ids if abs(sta_coords[s][1] - center_y) < 0.001] + north_stations
    ns_stations = list(dict.fromkeys(ns_stations))
    if len(ns_stations) >= 2:
        filtered = [ns_stations[0]]
        for s in ns_stations[1:]:
            d = dist_m(sta_coords[filtered[-1]], sta_coords[s])
            if MIN_STA_DIST <= d <= MAX_STA_DIST * 1.5:
                filtered.append(s)
        if len(filtered) >= 2:
            lines.append({
                'name': '南北线',
                'stations': filtered,
                'coords': [sta_coords[s] for s in filtered],
            })

    # 剩余站: 分配到最近的线
    used = set()
    for line in lines:
        used.update(line['stations'])

    remaining = [s for s in sta_ids if s not in used]
    for s in remaining:
        # 找最近的线和站
        best_line = 0
        best_d = 1e9
        for i, line in enumerate(lines):
            for sta in line['stations']:
                d = dist_m(sta_coords[s], sta_coords[sta])
                if d < best_d:
                    best_d, best_line = d, i
        # 插入到合适位置
        line = lines[best_line]
        # 找最近的两个相邻站之间插入
        best_pos = len(line['stations'])
        min_insert_dist = 1e9
        for pos in range(len(line['stations'])):
            d = dist_m(sta_coords[s], sta_coords[line['stations'][pos]])
            if d < min_insert_dist:
                min_insert_dist = d
                best_pos = pos
        line['stations'].insert(best_pos, s)
        line['coords'].insert(best_pos, sta_coords[s])

    # 确保线路连通
    print("  检查连通性...")
    G_lines = nx.Graph()
    for i in range(len(lines)):
        G_lines.add_node(i)

    # 检查共享站
    for i in range(len(lines)):
        for j in range(i+1, len(lines)):
            shared = set(lines[i]['stations']) & set(lines[j]['stations'])
            if shared:
                G_lines.add_edge(i, j, weight=0)

    if not nx.is_connected(G_lines) and len(lines) > 1:
        # 添加换乘连接
        components = list(nx.connected_components(G_lines))
        for comp_i in range(len(components)-1):
            line_i = list(components[comp_i])[0]
            line_j = list(components[comp_i+1])[0]
            # 找最近的一对站
            best_d, best_pair = 1e9, None
            for si in lines[line_i]['stations']:
                for sj in lines[line_j]['stations']:
                    d = dist_m(sta_coords[si], sta_coords[sj])
                    if d < best_d:
                        best_d, best_pair = d, (si, sj)
            if best_pair:
                lines.append({
                    'name': f'换乘连接',
                    'stations': list(best_pair),
                    'coords': [sta_coords[s] for s in best_pair],
                })
                print(f"    添加换乘: {best_pair[0]} ↔ {best_pair[1]} ({best_d:.0f}m)")

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
                    i += 1
                else:
                    new_path.append(B)
                    i += 1
            new_path.append(path[-1])
            path = new_path
        line['smooth_coords'] = [(float(p[0]), float(p[1])) for p in path]

# ═══════════════════════════════════════════════════════════════
# 第6步: 计算4个指标 (含换乘惩罚)
# ═══════════════════════════════════════════════════════════════
def calc_metrics(gdf_sta, lines, gdf_pop, coords, pops, cover_sets):
    """计算4个指标，换乘1次 = 3站地铁时间"""
    print("第6步: 计算指标...")

    # 1. 建站成本
    n_stations = len(gdf_sta)

    # 2. 线路里程
    total_length = 0
    for line in lines:
        for i in range(len(line['smooth_coords'])-1):
            total_length += dist_m(line['smooth_coords'][i], line['smooth_coords'][i+1])

    # 3. 覆盖度
    selected_indices = list(gdf_sta['sta_idx'])
    covered = set()
    for idx in selected_indices:
        covered |= cover_sets[idx]
    coverage = sum(pops[j] for j in covered) / pops.sum()

    # 4. 平均旅速 (含换乘惩罚)
    # 每条线路的站集合
    line_station_sets = [set(line['stations']) for line in lines]
    
    sta_coords = {s['sta_idx']: (s.geometry.x, s.geometry.y) for _, s in gdf_sta.iterrows()}
    sta_ids = list(sta_coords.keys())
    
    speeds = []
    for i in range(min(len(sta_ids), 20)):
        for j in range(i+1, min(len(sta_ids), 20)):
            si, sj = sta_ids[i], sta_ids[j]
            d = dist_m(sta_coords[si], sta_coords[sj])
            if d < 100: continue
            
            # 检查是否需要换乘
            same_line = False
            for ls in line_station_sets:
                if si in ls and sj in ls:
                    same_line = True
                    break
            
            n_mid = max(0, int(d / 1500) - 1)
            if same_line:
                # 同线: 步行 + 乘车
                t = T_ACCESS_S + d/V_MAX + n_mid*(T_STOP_S+T_ACCEL_S) + T_EGRESS_S
            else:
                # 换乘: 步行 + 乘车 + 换乘(3站时间) + 乘车 + 步行
                t = T_ACCESS_S + d/V_MAX + n_mid*(T_STOP_S+T_ACCEL_S) + T_TRANSFER_S + T_EGRESS_S
            
            if t > 0:
                speeds.append(d / t * 3.6)
    
    avg_speed = np.mean(speeds) if speeds else 0

    print(f"  1. 建站成本: {n_stations}站")
    print(f"  2. 线路里程: {total_length/1000:.1f}km")
    print(f"  3. 覆盖度: {coverage*100:.1f}%")
    print(f"  4. 平均旅速: {avg_speed:.1f}km/h (换乘惩罚={T_TRANSFER_S:.0f}s)")

    return {
        'n_stations': n_stations,
        'total_length_km': total_length / 1000,
        'coverage': coverage,
        'avg_speed_kmh': avg_speed,
    }

# ═══════════════════════════════════════════════════════════════
# 第7步: 可视化
# ═══════════════════════════════════════════════════════════════
def visualize(gdf_pop, gdf_sta, lines, G_road, metrics):
    print("第7步: 可视化...")
    west, south, east, north = BBOX
    fig, axes = plt.subplots(2, 2, figsize=(16, 14))
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']

    # 左上: 人口分布
    ax = axes[0,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#cccccc', edge_linewidth=0.3, show=False)
    gdf_pop.plot(ax=ax, column='pop', cmap='YlOrRd', markersize=3, alpha=0.7, legend=True)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('人口分布', fontsize=13)

    # 右上: 选站
    ax = axes[0,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=2, alpha=0.3)
    gdf_sta.plot(ax=ax, color='red', markersize=50, edgecolor='black', linewidth=0.5, zorder=5)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title(f'MCLP选站 {len(gdf_sta)}个', fontsize=13)

    # 左下: 线路
    ax = axes[1,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.15, show=False)
    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        sc = line['smooth_coords']
        ax.plot([p[0] for p in sc], [p[1] for p in sc], '-', color=c, linewidth=3, alpha=0.8, label=line['name'])
    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=50, zorder=5)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('精简线路 (东西线+南北线)', fontsize=13)
    ax.legend(fontsize=9)

    # 右下: 最终方案
    ax = axes[1,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#dddddd', edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=1, alpha=0.1)
    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        sc = line['smooth_coords']
        ax.plot([p[0] for p in sc], [p[1] for p in sc], '-', color=c, linewidth=3, alpha=0.85)
    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=60, zorder=5)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    title = (f'最终方案\n'
             f'建站: {metrics["n_stations"]}站 | 里程: {metrics["total_length_km"]:.1f}km\n'
             f'覆盖: {metrics["coverage"]*100:.1f}% | 旅速: {metrics["avg_speed_kmh"]:.1f}km/h\n'
             f'换乘惩罚: {T_TRANSFER_S:.0f}s (=3站地铁时间)')
    ax.set_title(title, fontsize=12)

    plt.tight_layout()
    plt.savefig(f'{OUTPUT}/metro_plan_v10.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  已保存: {OUTPUT}/metro_plan_v10.png")

def export_geojson(gdf_sta, lines):
    fc = {"type":"FeatureCollection","features":[]}
    for _, s in gdf_sta.iterrows():
        fc['features'].append({"type":"Feature","geometry":{"type":"Point","coordinates":[s.geometry.x,s.geometry.y]},"properties":{"sta_idx":int(s['sta_idx']),"covered_pop":int(s['covered_pop'])}})
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
    print("地铁规划 v10 — 精简线路 + 换乘惩罚")
    print(f"换乘1次 = {T_TRANSFER_S:.0f}s = 3站地铁时间")
    print("="*60)

    buildings, G_road = get_data()
    gdf_pop = make_population(buildings)
    gdf_sta, coords, pops, cover_sets = mclp_select(gdf_pop)
    lines = build_fewer_lines(gdf_sta)
    smooth_lines(lines)
    metrics = calc_metrics(gdf_sta, lines, gdf_pop, coords, pops, cover_sets)
    export_geojson(gdf_sta, lines)
    visualize(gdf_pop, gdf_sta, lines, G_road, metrics)

    print("\n✓ 完成!")

if __name__ == '__main__':
    main()
