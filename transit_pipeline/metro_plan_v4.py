#!/usr/bin/env python3
"""
地铁规划 v4
- 人口OD均匀 (每个点到其他所有点概率相同)
- 目标: 最大化所有OD对的平均旅速
- 旅速模型: 站间距影响最高车速, 含停站/加减速/换乘惩罚
- 叠加OSM路网底图
"""

import os, json, numpy as np, geopandas as gpd
import osmnx as ox
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from shapely.geometry import Point
import warnings
warnings.filterwarnings('ignore')

# ── 字体 ────────────────────────────────────────────────────
for p in ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
          '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
          '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc']:
    if os.path.exists(p):
        fm.fontManager.addfont(p)
        _prop = fm.FontProperties(fname=p)
        plt.rcParams['font.family'] = _prop.get_name()
        break
plt.rcParams['axes.unicode_minus'] = False

# ── 参数 ────────────────────────────────────────────────────
BBOX = (121.48, 31.20, 121.56, 31.26)
OUTPUT = '/smb/j/SHT/COURSE-10/CS240/pj/transit_pipeline/output'
os.makedirs(OUTPUT, exist_ok=True)

TOTAL_POP    = 500_000
GRID_SIZE_M  = 1000       # 聚类网格 1km
COVERAGE_R   = 800        # 覆盖半径 m
MIN_STA_DIST = 1000       # 最小站距 m

# ── 旅速模型参数 ─────────────────────────────────────────────
V_MAX_KMH    = 80         # 地铁最高运行速度 km/h
T_STOP_S     = 30         # 停站时间 s
T_ACCEL_S    = 20         # 每站加减速额外时间 s
T_TRANSFER_S = 5 * 60     # 换乘时间 5min
T_ACCESS_S   = 5 * 60     # 步行进站时间 5min (平均)
T_EGRESS_S   = 5 * 60     # 步行出站时间 5min

V_MAX = V_MAX_KMH / 3.6  # m/s

# ═══════════════════════════════════════════════════════════════
# 旅速计算
# ═══════════════════════════════════════════════════════════════
def travel_time_oneline(dist_m, n_stations):
    """
    单条线路两点间旅行时间 (秒)
    dist_m: 直线距离 (米)
    n_stations: 中间站数 (不含首尾)
    """
    if dist_m <= 0:
        return 0
    # 运行时间 = 距离 / 最高车速 (简化: 不考虑站间加速不足)
    t_run = dist_m / V_MAX
    # 停站 + 加减速惩罚
    t_penalty = n_stations * (T_STOP_S + T_ACCEL_S)
    return t_run + t_penalty

def avg_travel_speed(gdf_sta, metro_lines, gdf_pop):
    """
    计算所有OD对的平均旅速 (km/h)
    假设: 每个人从家到任意其他点的概率均匀
    模型:
      - 在同一线上: 步行进站 + 乘车 + 步行出站
      - 需换乘: 步行进站 + 乘车1 + 换乘 + 乘车2 + 步行出站
    """
    west, south, east, north = BBOX
    lat_mid = (south + north) / 2
    m_lon = 111_000 * np.cos(np.radians(lat_mid))
    m_lat = 111_000

    # 每站所属线路
    station_line = {}
    for line in metro_lines:
        for sid in line['stations']:
            station_line[sid] = line['name']

    # 车站坐标 {id: (x, y)}
    sta_xy = {}
    for _, s in gdf_sta.iterrows():
        sta_xy[s['station_id']] = (s.geometry.x, s.geometry.y)

    # 计算任意两站间的旅行时间 (含步行+换乘)
    sta_ids = list(sta_xy.keys())
    n_sta = len(sta_ids)
    tt_matrix = np.full((n_sta, n_sta), np.inf)  # 旅行时间 (秒)
    dist_matrix = np.zeros((n_sta, n_sta))        # 直线距离 (米)

    for i in range(n_sta):
        for j in range(i+1, n_sta):
            si, sj = sta_ids[i], sta_ids[j]
            xi, yi = sta_xy[si]
            xj, yj = sta_xy[sj]
            dx = (xi - xj) * m_lon
            dy = (yi - yj) * m_lat
            d = np.sqrt(dx**2 + dy**2)
            dist_matrix[i, j] = d
            dist_matrix[j, i] = d

            # 在同一条线上?
            same_line = (station_line.get(si) == station_line.get(sj))

            if same_line:
                # 计算中间站数 (简化: 用距离/站距估算)
                n_mid = max(0, int(d / 1500) - 1)  # 平均站距1.5km
                tt = T_ACCESS_S + travel_time_oneline(d, n_mid) + T_EGRESS_S
            else:
                # 需换乘: 找最近换乘站
                # 简化: 假设换乘一次, 各自线路内走一半距离
                d_half = d / 2
                n_mid1 = max(0, int(d_half / 1500) - 1)
                n_mid2 = max(0, int(d_half / 1500) - 1)
                tt = (T_ACCESS_S +
                      travel_time_oneline(d_half, n_mid1) +
                      T_TRANSFER_S +
                      travel_time_oneline(d_half, n_mid2) +
                      T_EGRESS_S)

            tt_matrix[i, j] = tt
            tt_matrix[j, i] = tt

    # 计算每个OD对的旅速 = 距离 / 时间
    speeds = []
    for i in range(n_sta):
        for j in range(i+1, n_sta):
            if tt_matrix[i, j] > 0 and tt_matrix[i, j] < np.inf:
                v = dist_matrix[i, j] / tt_matrix[i, j] * 3.6  # km/h
                speeds.append(v)

    avg_v = np.mean(speeds) if speeds else 0
    return avg_v, speeds

# ═══════════════════════════════════════════════════════════════
# 数据获取与处理 (同v3)
# ═══════════════════════════════════════════════════════════════
def get_data():
    print("第1步: 获取OSM数据 (建筑物 + 路网)...")
    buildings = ox.features_from_bbox(bbox=BBOX, tags={'building': True})
    G_road = ox.graph_from_bbox(bbox=BBOX, network_type='drive')
    print(f"  建筑物: {len(buildings)}, 路网节点: {len(G_road.nodes)}, 边: {len(G_road.edges)}")
    return buildings, G_road

def make_population(buildings):
    print("第2步: 生成人口分布...")
    res_types = ['apartments','house','residential','dormitory','yes']
    if 'building' in buildings.columns:
        mask = buildings['building'].isin(res_types) | buildings['building'].isna()
        bld = buildings[mask].copy()
    else:
        bld = buildings.copy()
    bld = bld[bld.geometry.is_valid & ~bld.geometry.is_empty]
    bld_m = bld.to_crs(epsg=3857)
    bld_m['area'] = bld_m.geometry.area
    bld_m = bld_m[bld_m['area'] > 10]
    total_area = bld_m['area'].sum()
    bld_m['pop'] = (bld_m['area'] / total_area * TOTAL_POP).astype(int)
    bld_wgs = bld_m.to_crs(epsg=4326)
    bld_wgs['centroid'] = bld_wgs.geometry.centroid
    gdf_pop = gpd.GeoDataFrame({
        'geometry': bld_wgs['centroid'].values,
        'pop': bld_wgs['pop'].values
    }, crs='EPSG:4326')
    print(f"  总人口: {gdf_pop['pop'].sum()}, 点数: {len(gdf_pop)}")
    return gdf_pop

def cluster_population(gdf_pop):
    print("第3步: 1km网格聚类...")
    west, south, east, north = BBOX
    lat_mid = (south + north) / 2
    m_lon = 111_000 * np.cos(np.radians(lat_mid))
    m_lat = 111_000
    grid_dx = GRID_SIZE_M / m_lon
    grid_dy = GRID_SIZE_M / m_lat
    nx_g = max(1, int(np.ceil((east - west) / grid_dx)))
    ny_g = max(1, int(np.ceil((north - south) / grid_dy)))
    print(f"  网格: {nx_g}×{ny_g} = {nx_g*ny_g}")
    coords = np.array([[g.x, g.y] for g in gdf_pop.geometry])
    pops = gdf_pop['pop'].values
    candidates = []
    for i in range(ny_g):
        for j in range(nx_g):
            x0 = west + j * grid_dx; x1 = x0 + grid_dx
            y0 = south + i * grid_dy; y1 = y0 + grid_dy
            mask = ((coords[:,0] >= x0) & (coords[:,0] < x1) &
                    (coords[:,1] >= y0) & (coords[:,1] < y1))
            if mask.sum() == 0: continue
            cell_pop = pops[mask].sum()
            cell_coords = coords[mask]
            if cell_pop > 0:
                wx = np.average(cell_coords[:,0], weights=pops[mask])
                wy = np.average(cell_coords[:,1], weights=pops[mask])
            else:
                wx = cell_coords[:,0].mean(); wy = cell_coords[:,1].mean()
            candidates.append({'geometry': Point(wx, wy), 'pop': int(cell_pop),
                               'grid_i': i, 'grid_j': j})
    gdf_cand = gpd.GeoDataFrame(candidates, crs='EPSG:4326')
    print(f"  候选: {len(gdf_cand)}")
    return gdf_cand

# ═══════════════════════════════════════════════════════════════
# 选站: 网格搜索最优站距 → 最大化旅速
# ═══════════════════════════════════════════════════════════════
def optimize_station_spacing(gdf_cand, gdf_pop):
    """
    暴力搜索不同站距 (1000m ~ 2500m, 步长100m),
    对每种站距做贪心选站, 计算平均旅速, 取最优
    """
    print("第4步: 网格搜索最优站距 (最大化平均旅速)...")
    lat_mid = (BBOX[1] + BBOX[3]) / 2
    m_lon = 111_000 * np.cos(np.radians(lat_mid))
    m_lat = 111_000

    results = []
    for min_dist in range(1000, 2600, 200):
        # 贪心选站
        df = gdf_cand.sort_values('pop', ascending=False).reset_index(drop=True)
        selected = []
        for _, row in df.iterrows():
            pt = row.geometry
            ok = True
            for sp in selected:
                dx = (pt.x - sp.x) * m_lon
                dy = (pt.y - sp.y) * m_lat
                if np.sqrt(dx**2 + dy**2) < min_dist:
                    ok = False; break
            if ok:
                selected.append(pt)

        if len(selected) < 2:
            continue

        gdf_sel = gpd.GeoDataFrame(
            [{'geometry': p, 'station_id': f'S{i+1}'} for i, p in enumerate(selected)],
            crs='EPSG:4326')
        lines = build_metro_lines(gdf_sel)
        avg_v, _ = avg_travel_speed(gdf_sel, lines, gdf_pop)

        results.append({
            'min_dist': min_dist,
            'n_stations': len(selected),
            'avg_speed': avg_v,
            'gdf': gdf_sel,
            'lines': lines,
        })
        print(f"    站距≥{min_dist}m → {len(selected)}站, 平均旅速 {avg_v:.1f} km/h")

    if not results:
        print("  错误: 无可行方案")
        return None, None, None

    best = max(results, key=lambda x: x['avg_speed'])
    print(f"\n  ✓ 最优站距: ≥{best['min_dist']}m")
    print(f"    车站数: {best['n_stations']}")
    print(f"    平均旅速: {best['avg_speed']:.1f} km/h")

    # 计算覆盖率
    sta_coords = np.array([[g.x, g.y] for g in best['gdf'].geometry])
    all_coords = np.array([[g.x, g.y] for g in gdf_cand.geometry])
    all_pops = gdf_cand['pop'].values
    covered = 0
    for k in range(len(sta_coords)):
        dx = (all_coords[:,0] - sta_coords[k,0]) * m_lon
        dy = (all_coords[:,1] - sta_coords[k,1]) * m_lat
        dists = np.sqrt(dx**2 + dy**2)
        covered += all_pops[dists < COVERAGE_R].sum()
    print(f"    覆盖人口: {covered} / {all_pops.sum()}")

    return best['gdf'], best['lines'], results

def build_metro_lines(gdf_sta):
    df = gdf_sta.copy()
    df['lon'] = df.geometry.x; df['lat'] = df.geometry.y
    lat_mid = df['lat'].median()
    north = df[df['lat'] >= lat_mid].sort_values('lon')
    south = df[df['lat'] < lat_mid].sort_values('lon')
    lines = []
    if len(north) >= 2:
        lines.append({'name': 'Line 1 (北线)',
                       'coords': [(g.x, g.y) for g in north.geometry],
                       'stations': north['station_id'].tolist()})
    if len(south) >= 2:
        lines.append({'name': 'Line 2 (南线)',
                       'coords': [(g.x, g.y) for g in south.geometry],
                       'stations': south['station_id'].tolist()})
    # 换乘
    if len(north) >= 1 and len(south) >= 1:
        best_d, best_p = 1e9, None
        for _, ni in north.iterrows():
            for _, si in south.iterrows():
                dx = (ni.geometry.x - si.geometry.x) * 111000 * np.cos(np.radians(lat_mid))
                dy = (ni.geometry.y - si.geometry.y) * 111000
                d = np.sqrt(dx**2 + dy**2)
                if d < best_d:
                    best_d, best_p = d, (ni, si)
        if best_p and best_d < 3000:
            lines.append({'name': '换乘连接',
                           'coords': [(best_p[0].geometry.x, best_p[0].geometry.y),
                                      (best_p[1].geometry.x, best_p[1].geometry.y)],
                           'stations': [best_p[0]['station_id'], best_p[1]['station_id']]})
    return lines

# ═══════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════
def export_geojson(gdf_sta, gdf_pop, metro_lines):
    print("导出GeoJSON...")
    fc = {"type":"FeatureCollection","features":[]}
    for _, s in gdf_sta.iterrows():
        fc['features'].append({"type":"Feature",
            "geometry":{"type":"Point","coordinates":[s.geometry.x, s.geometry.y]},
            "properties":{"id":s['station_id']}})
    with open(f'{OUTPUT}/stations.geojson','w') as f: json.dump(fc, f, indent=2)
    fc2 = {"type":"FeatureCollection","features":[]}
    for line in metro_lines:
        fc2['features'].append({"type":"Feature",
            "geometry":{"type":"LineString","coordinates":line['coords']},
            "properties":{"name":line['name']}})
    with open(f'{OUTPUT}/metro_lines.geojson','w') as f: json.dump(fc2, f, indent=2)

# ═══════════════════════════════════════════════════════════════
# 可视化: 底图=OSM路网 + 车站 + 线路
# ═══════════════════════════════════════════════════════════════
def visualize(gdf_pop, gdf_cand, gdf_sta, metro_lines, G_road, opt_results):
    print("可视化...")
    west, south, east, north = BBOX
    fig, axes = plt.subplots(2, 2, figsize=(16, 14))

    # ── 左上: 人口 + OSM路网 ──
    ax = axes[0, 0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#cccccc',
                  edge_linewidth=0.3, show=False)
    gdf_pop.plot(ax=ax, column='pop', cmap='YlOrRd', markersize=3, alpha=0.7, legend=True)
    ax.set_xlim(west, east); ax.set_ylim(south, north)
    ax.set_title('人口分布 + OSM路网底图', fontsize=13)

    # ── 右上: 1km聚类候选 ──
    ax = axes[0, 1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee',
                  edge_linewidth=0.2, show=False)
    gdf_cand.plot(ax=ax, column='pop', cmap='YlGn', markersize=40,
                  edgecolor='black', linewidth=0.5, legend=True)
    ax.set_xlim(west, east); ax.set_ylim(south, north)
    ax.set_title(f'1km网格聚类 → {len(gdf_cand)}个候选', fontsize=13)

    # ── 左下: 旅速 vs 站距曲线 ──
    ax = axes[1, 0]
    if opt_results:
        dists = [r['min_dist'] for r in opt_results]
        speeds = [r['avg_speed'] for r in opt_results]
        ns = [r['n_stations'] for r in opt_results]
        ax.plot(dists, speeds, 'o-', color='#1f77b4', linewidth=2, markersize=8)
        ax.set_xlabel('最小站距 (m)', fontsize=11)
        ax.set_ylabel('平均旅速 (km/h)', fontsize=11, color='#1f77b4')
        ax.tick_params(axis='y', labelcolor='#1f77b4')
        ax2 = ax.twinx()
        ax2.plot(dists, ns, 's--', color='#ff7f0e', linewidth=1.5, markersize=6)
        ax2.set_ylabel('车站数量', fontsize=11, color='#ff7f0e')
        ax2.tick_params(axis='y', labelcolor='#ff7f0e')
        best_r = max(opt_results, key=lambda x: x['avg_speed'])
        ax.axvline(best_r['min_dist'], color='red', linestyle=':', alpha=0.7)
        ax.set_title(f'旅速优化曲线 (最优站距≥{best_r["min_dist"]}m)', fontsize=13)
    else:
        ax.text(0.5, 0.5, '无数据', ha='center', va='center', transform=ax.transAxes)

    # ── 右下: 最优方案 + OSM路网 ──
    ax = axes[1, 1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#dddddd',
                  edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=1, alpha=0.15)

    colors = ['#1f77b4', '#ff7f0e', '#2ca02c']
    for k, line in enumerate(metro_lines):
        xs = [c[0] for c in line['coords']]
        ys = [c[1] for c in line['coords']]
        ax.plot(xs, ys, '-', color=colors[k % 3], linewidth=3, alpha=0.8, label=line['name'])
        ax.plot(xs, ys, 'o', color=colors[k % 3], markersize=8)

    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=60, zorder=5)
    for _, s in gdf_sta.iterrows():
        ax.annotate(s['station_id'], xy=(s.geometry.x, s.geometry.y),
                    fontsize=7, ha='center', va='bottom', xytext=(0, 5),
                    textcoords='offset points', fontweight='bold')
        circle = plt.Circle((s.geometry.x, s.geometry.y),
                            COVERAGE_R / 111000, color='green', alpha=0.06)
        ax.add_patch(circle)

    ax.set_xlim(west, east); ax.set_ylim(south, north)
    ax.set_title('最优地铁方案 + OSM路网', fontsize=13)
    ax.legend(loc='upper right')

    plt.tight_layout()
    plt.savefig(f'{OUTPUT}/metro_plan_v4.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  已保存: {OUTPUT}/metro_plan_v4.png")

# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("地铁规划 v4 — 最大化平均旅速")
    print("OD假设: 均匀 (每个点到其他点概率相同)")
    print("旅速模型: 含停站/加减速/换乘/步行惩罚")
    print("=" * 60)

    buildings, G_road = get_data()
    gdf_pop = make_population(buildings)
    gdf_cand = cluster_population(gdf_pop)
    gdf_sta, metro_lines, opt_results = optimize_station_spacing(gdf_cand, gdf_pop)

    if gdf_sta is None:
        print("失败"); return

    # 计算最终方案旅速
    avg_v, speeds = avg_travel_speed(gdf_sta, metro_lines, gdf_pop)
    print(f"\n最终方案平均旅速: {avg_v:.1f} km/h")
    print(f"旅速分布: 中位数={np.median(speeds):.1f}, "
          f"最小={np.min(speeds):.1f}, 最大={np.max(speeds):.1f}")

    export_geojson(gdf_sta, gdf_pop, metro_lines)
    visualize(gdf_pop, gdf_cand, gdf_sta, metro_lines, G_road, opt_results)

    print("\n✓ 完成!")

if __name__ == '__main__':
    main()
