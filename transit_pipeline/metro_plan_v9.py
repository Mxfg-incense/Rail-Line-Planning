#!/usr/bin/env python3
"""
地铁规划 v9 — 方向性线路 + 4目标优化
指标:
  1. 建站成本 (车站数)
  2. 线路里程成本 (总里程)
  3. 800m覆盖度
  4. 平均旅速

算法:
  1. MCLP选候选站
  2. 选2-3个换乘枢纽
  3. 从枢纽延伸方向性线路 (东西/南北)
  4. 优化4个目标
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
def mclp_select(gdf_pop, n_sta=40):
    """MCLP选候选站"""
    print(f"第3步: MCLP选候选站 ({n_sta}个)...")
    coords = np.array([[g.x, g.y] for g in gdf_pop.geometry])
    pops = gdf_pop['pop'].values
    n = len(coords)

    # 预计算覆盖关系
    cover_sets = []
    for i in range(n):
        dx = (coords[:, 0] - coords[i, 0]) * M_LON
        dy = (coords[:, 1] - coords[i, 1]) * M_LAT
        dists = np.sqrt(dx**2 + dy**2)
        cover_sets.append(set(np.where(dists < WALK_R_M)[0]))

    # 贪心MCLP
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
# 第4步: 选换乘枢纽 + 方向性线路
# ═══════════════════════════════════════════════════════════════
def select_hubs_and_lines(gdf_sta, coords, pops):
    """
    选2-3个换乘枢纽（覆盖人口最多的站）
    从每个枢纽延伸4方向线路（东/西/南/北）
    确保所有线路连通
    """
    print("第4步: 选换乘枢纽 + 方向性线路...")

    # 选覆盖人口最多的2-3个站作为枢纽
    df = gdf_sta.sort_values('covered_pop', ascending=False)
    n_hubs = min(3, len(df))
    hub_indices = list(df.index[:n_hubs])

    print(f"  换乘枢纽: {n_hubs}个")

    # 从每个枢纽延伸4方向线路
    directions = {
        '东': (1, 0),
        '西': (-1, 0),
        '北': (0, 1),
        '南': (0, -1),
    }

    lines = []
    used_stations = set()

    for hub_idx in hub_indices:
        hub = gdf_sta.loc[hub_idx]
        hub_coord = (hub.geometry.x, hub.geometry.y)

        for dir_name, (dx, dy) in directions.items():
            candidates = []
            for _, sta in gdf_sta.iterrows():
                if sta.name in used_stations and sta.name != hub_idx:
                    continue
                sta_coord = (sta.geometry.x, sta.geometry.y)
                vec = (sta_coord[0] - hub_coord[0], sta_coord[1] - hub_coord[1])
                proj = vec[0] * dx + vec[1] * dy
                if proj > 0:
                    perp = abs(vec[0] * dy - vec[1] * dx)
                    candidates.append((sta.name, proj, perp, sta_coord))

            if not candidates:
                continue

            candidates.sort(key=lambda x: x[1])
            line_stations = [hub_idx]
            line_coords = [hub_coord]

            for cand_idx, proj, perp, cand_coord in candidates[:5]:
                if cand_idx in used_stations and cand_idx != hub_idx:
                    continue
                last_coord = line_coords[-1]
                d = dist_m(last_coord, cand_coord)
                if MIN_STA_DIST <= d <= MAX_STA_DIST:
                    line_stations.append(cand_idx)
                    line_coords.append(cand_coord)

            if len(line_stations) >= 2:
                line_name = f"Hub{gdf_sta.loc[hub_idx,'sta_idx']}_{dir_name}"
                lines.append({
                    'name': line_name,
                    'stations': line_stations,
                    'coords': line_coords,
                    'hub': hub_idx,
                })
                used_stations.update(line_stations)
                print(f"    {line_name}: {len(line_stations)}站")

    # 确保所有线路连通：检查每条线路是否与其他线路有共享站
    # 如果没有共享站，添加最短连接
    print("  检查连通性...")
    all_connected = False
    max_attempts = 10
    attempt = 0

    while not all_connected and attempt < max_attempts:
        attempt += 1
        # 构建线路连通图
        G_lines = nx.Graph()
        for i in range(len(lines)):
            G_lines.add_node(i)

        # 检查每对线路是否有共享站
        for i in range(len(lines)):
            for j in range(i+1, len(lines)):
                shared = set(lines[i]['stations']) & set(lines[j]['stations'])
                if shared:
                    G_lines.add_edge(i, j, weight=0)  # 有共享站

        # 如果线路图连通，则所有线路连通
        if nx.is_connected(G_lines):
            all_connected = True
            print("    ✓ 所有线路已连通")
            break

        # 找不连通的分量，添加最短连接
        components = list(nx.connected_components(G_lines))
        print(f"    连通分量: {len(components)}个, 添加连接...")

        # 找最近的一对站连接不同分量
        best_d = 1e9
        best_info = None
        for comp_i in range(len(components)):
            for comp_j in range(comp_i+1, len(components)):
                for line_i in components[comp_i]:
                    for line_j in components[comp_j]:
                        for si in lines[line_i]['stations']:
                            for sj in lines[line_j]['stations']:
                                coord_i = gdf_sta.loc[si].geometry
                                coord_j = gdf_sta.loc[sj].geometry
                                d = dist_m((coord_i.x, coord_i.y), (coord_j.x, coord_j.y))
                                if d < best_d:
                                    best_d = d
                                    best_info = (line_i, line_j, si, sj)

        if best_info:
            line_i, line_j, si, sj = best_info
            # 添加换乘连接线
            coord_i = (gdf_sta.loc[si].geometry.x, gdf_sta.loc[si].geometry.y)
            coord_j = (gdf_sta.loc[sj].geometry.x, gdf_sta.loc[sj].geometry.y)
            lines.append({
                'name': f'换乘{len(lines)+1}',
                'stations': [si, sj],
                'coords': [coord_i, coord_j],
                'hub': si,
            })
            print(f"    添加换乘: {gdf_sta.loc[si,'sta_idx']} ↔ {gdf_sta.loc[sj,'sta_idx']} ({best_d:.0f}m)")

    return lines, hub_indices

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
# 第6步: 计算4个指标
# ═══════════════════════════════════════════════════════════════
def calc_metrics(gdf_sta, lines, gdf_pop, coords, pops, cover_sets):
    """计算4个指标"""
    print("第6步: 计算指标...")

    # 1. 建站成本 (车站数)
    n_stations = len(gdf_sta)

    # 2. 线路里程成本 (总里程)
    total_length = 0
    for line in lines:
        for i in range(len(line['smooth_coords'])-1):
            total_length += dist_m(line['smooth_coords'][i], line['smooth_coords'][i+1])

    # 3. 800m覆盖度
    selected_indices = list(gdf_sta['sta_idx'])
    covered = set()
    for idx in selected_indices:
        covered |= cover_sets[idx]
    coverage = sum(pops[j] for j in covered) / pops.sum()

    # 4. 平均旅速
    # 简化: 假设OD均匀，计算所有站对的平均旅速
    sta_coords = [(s.geometry.x, s.geometry.y) for _, s in gdf_sta.iterrows()]
    speeds = []
    for i in range(min(len(sta_coords), 20)):
        for j in range(i+1, min(len(sta_coords), 20)):
            d = dist_m(sta_coords[i], sta_coords[j])
            if d < 100: continue
            n_mid = max(0, int(d / 1500) - 1)
            t = T_ACCESS_S + d/V_MAX + n_mid*(T_STOP_S+T_ACCEL_S) + T_EGRESS_S
            if t > 0:
                speeds.append(d / t * 3.6)
    avg_speed = np.mean(speeds) if speeds else 0

    print(f"  1. 建站成本: {n_stations}站")
    print(f"  2. 线路里程: {total_length/1000:.1f}km")
    print(f"  3. 覆盖度: {coverage*100:.1f}%")
    print(f"  4. 平均旅速: {avg_speed:.1f}km/h")

    return {
        'n_stations': n_stations,
        'total_length_km': total_length / 1000,
        'coverage': coverage,
        'avg_speed_kmh': avg_speed,
    }

# ═══════════════════════════════════════════════════════════════
# 第7步: 可视化
# ═══════════════════════════════════════════════════════════════
def visualize(gdf_pop, gdf_sta, lines, G_road, metrics, hub_indices):
    print("第7步: 可视化...")
    west, south, east, north = BBOX
    fig, axes = plt.subplots(2, 2, figsize=(16, 14))
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
              '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']

    # 左上: 人口分布
    ax = axes[0,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#cccccc', edge_linewidth=0.3, show=False)
    gdf_pop.plot(ax=ax, column='pop', cmap='YlOrRd', markersize=3, alpha=0.7, legend=True)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('人口分布', fontsize=13)

    # 右上: MCLP选站 + 换乘枢纽
    ax = axes[0,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=2, alpha=0.3)
    gdf_sta.plot(ax=ax, color='red', markersize=40, edgecolor='black', linewidth=0.5, zorder=5)
    # 标记换乘枢纽
    for idx in hub_indices:
        hub = gdf_sta.loc[idx]
        ax.scatter(hub.geometry.x, hub.geometry.y, s=200, c='yellow', 
                   edgecolors='black', linewidths=2, zorder=6, marker='s')
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title(f'MCLP选站 {len(gdf_sta)}个 (黄=换乘枢纽)', fontsize=13)

    # 左下: 方向性线路
    ax = axes[1,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.15, show=False)
    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        sc = line['smooth_coords']
        ax.plot([p[0] for p in sc], [p[1] for p in sc], '-', color=c, linewidth=2.5, alpha=0.8, label=line['name'])
    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=40, zorder=5)
    for idx in hub_indices:
        hub = gdf_sta.loc[idx]
        ax.scatter(hub.geometry.x, hub.geometry.y, s=200, c='yellow', 
                   edgecolors='black', linewidths=2, zorder=6, marker='s')
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('方向性线路 (从枢纽延伸)', fontsize=13)
    ax.legend(fontsize=7, loc='upper right')

    # 右下: 最终方案 + 指标
    ax = axes[1,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#dddddd', edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=1, alpha=0.1)
    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        sc = line['smooth_coords']
        ax.plot([p[0] for p in sc], [p[1] for p in sc], '-', color=c, linewidth=3, alpha=0.85)
    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=50, zorder=5)
    for idx in hub_indices:
        hub = gdf_sta.loc[idx]
        ax.scatter(hub.geometry.x, hub.geometry.y, s=200, c='yellow', 
                   edgecolors='black', linewidths=2, zorder=6, marker='s')
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    title = (f'最终方案\n'
             f'建站: {metrics["n_stations"]}站 | 里程: {metrics["total_length_km"]:.1f}km\n'
             f'覆盖: {metrics["coverage"]*100:.1f}% | 旅速: {metrics["avg_speed_kmh"]:.1f}km/h')
    ax.set_title(title, fontsize=12)

    plt.tight_layout()
    plt.savefig(f'{OUTPUT}/metro_plan_v9.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  已保存: {OUTPUT}/metro_plan_v9.png")

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
    print("地铁规划 v9 — 方向性线路 + 4目标优化")
    print("="*60)

    buildings, G_road = get_data()
    gdf_pop = make_population(buildings)
    gdf_sta, coords, pops, cover_sets = mclp_select(gdf_pop)
    lines, hub_indices = select_hubs_and_lines(gdf_sta, coords, pops)
    smooth_lines(lines)
    metrics = calc_metrics(gdf_sta, lines, gdf_pop, coords, pops, cover_sets)
    export_geojson(gdf_sta, lines)
    visualize(gdf_pop, gdf_sta, lines, G_road, metrics, hub_indices)

    print("\n✓ 完成!")

if __name__ == '__main__':
    main()
