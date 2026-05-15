#!/usr/bin/env python3
"""
地铁规划 v11 — 线路优先，摒弃聚类
思路:
  1. 先画线路：东西向、南北向的直线（确定换乘站）
  2. 沿线加站：每条线每隔1-2km选一个站
  3. 站的位置：在线路附近选人口最密集的点
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
STA_INTERVAL = 1000      # 站距 1km
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
# 第3步: 线路优先 — 先画线路，再沿线加站
# ═══════════════════════════════════════════════════════════════
def line_first_design(gdf_pop):
    """
    线路优先设计:
    1. 先画东西向和南北向的直线（穿过人口密集区）
    2. 在每条线上每隔STA_INTERVAL选一个站
    3. 站的位置选在直线附近人口最密集的点
    """
    print("第3步: 线路优先设计...")
    
    coords = np.array([[g.x, g.y] for g in gdf_pop.geometry])
    pops = gdf_pop['pop'].values
    
    # 计算人口加权中心
    center_x = np.average(coords[:, 0], weights=pops)
    center_y = np.average(coords[:, 1], weights=pops)
    print(f"  人口中心: ({center_x:.4f}, {center_y:.4f})")
    
    # 计算区域范围
    west, south, east, north = BBOX
    
    # ── 东西线: 从西到东穿过人口中心 ──
    ew_line_y = center_y  # 东西线的y坐标 = 人口中心的y
    ew_line = LineString([(west, ew_line_y), (east, ew_line_y)])
    
    # ── 南北线: 从南到北穿过人口中心 ──
    ns_line_x = center_x  # 南北线的x坐标 = 人口中心的x
    ns_line = LineString([(ns_line_x, south), (ns_line_x, north)])
    
    # ── 换乘站: 两条线的交点 ──
    transfer_station = (center_x, center_y)
    print(f"  换乘站: ({center_x:.4f}, {center_y:.4f})")
    
    # ── 沿线加站 ──
    # 东西线上的站
    ew_stations = []
    # 从换乘站向西
    x = center_x
    while x > west:
        x -= STA_INTERVAL / M_LON
        if x < west: break
        # 找这个位置附近人口最密集的点
        best_point = find_densest_point_near_line(coords, pops, x, ew_line_y, 'ew')
        if best_point:
            ew_stations.append(best_point)
    ew_stations.reverse()
    # 换乘站
    ew_stations.append(transfer_station)
    # 从换乘站向东
    x = center_x
    while x < east:
        x += STA_INTERVAL / M_LON
        if x > east: break
        best_point = find_densest_point_near_line(coords, pops, x, ew_line_y, 'ew')
        if best_point:
            ew_stations.append(best_point)
    
    # 南北线上的站
    ns_stations = []
    # 从换乘站向南
    y = center_y
    while y > south:
        y -= STA_INTERVAL / M_LAT
        if y < south: break
        best_point = find_densest_point_near_line(coords, pops, ns_line_x, y, 'ns')
        if best_point:
            ns_stations.append(best_point)
    ns_stations.reverse()
    # 换乘站
    ns_stations.append(transfer_station)
    # 从换乘站向北
    y = center_y
    while y < north:
        y += STA_INTERVAL / M_LAT
        if y > north: break
        best_point = find_densest_point_near_line(coords, pops, ns_line_x, y, 'ns')
        if best_point:
            ns_stations.append(best_point)
    
    print(f"  东西线: {len(ew_stations)}站")
    print(f"  南北线: {len(ns_stations)}站")
    
    # 创建线路
    lines = []
    if len(ew_stations) >= 2:
        lines.append({
            'name': '东西线',
            'stations': ew_stations,
            'coords': ew_stations,
        })
    if len(ns_stations) >= 2:
        lines.append({
            'name': '南北线',
            'stations': ns_stations,
            'coords': ns_stations,
        })
    
    # 合并所有站（去重）
    all_stations = []
    seen = set()
    for line in lines:
        for sta in line['stations']:
            key = (round(sta[0], 6), round(sta[1], 6))
            if key not in seen:
                seen.add(key)
                all_stations.append(sta)
    
    # 创建车站GeoDataFrame
    gdf_sta = gpd.GeoDataFrame([{
        'geometry': Point(sta[0], sta[1]),
        'station_id': f'S{i+1}',
    } for i, sta in enumerate(all_stations)], crs='EPSG:4326')
    
    # 创建坐标到station_id的映射
    coord_to_id = {}
    for _, row in gdf_sta.iterrows():
        coord_to_id[(round(row.geometry.x, 6), round(row.geometry.y, 6))] = row['station_id']
    
    # 更新线路中的stations为station_id
    for line in lines:
        line['stations'] = [coord_to_id.get((round(sta[0], 6), round(sta[1], 6)), f'S{i+1}') 
                           for i, sta in enumerate(line['stations'])]
    
    # 计算覆盖人口
    cover_sets = []
    for _, sta in gdf_sta.iterrows():
        dx = (coords[:, 0] - sta.geometry.x) * M_LON
        dy = (coords[:, 1] - sta.geometry.y) * M_LAT
        dists = np.sqrt(dx**2 + dy**2)
        cover_sets.append(set(np.where(dists < WALK_R_M)[0]))
    
    covered = set()
    for cs in cover_sets:
        covered |= cs
    coverage = sum(pops[j] for j in covered) / pops.sum()
    
    print(f"  总车站: {len(gdf_sta)}, 覆盖: {coverage*100:.1f}%")
    
    return gdf_sta, lines, cover_sets, coords, pops

def find_densest_point_near_line(coords, pops, x, y, direction):
    """
    在指定位置附近找人口最密集的点
    """
    # 搜索范围: 500m
    search_r = 500 / 111000  # 转换为度
    
    if direction == 'ew':
        # 东西线: 找x附近、y在y±search_r范围内的最密集点
        mask = ((np.abs(coords[:, 0] - x) < search_r) & 
                (np.abs(coords[:, 1] - y) < search_r))
    else:
        # 南北线: 找y附近、x在x±search_r范围内的最密集点
        mask = ((np.abs(coords[:, 0] - x) < search_r) & 
                (np.abs(coords[:, 1] - y) < search_r))
    
    if mask.sum() == 0:
        # 没有找到点，返回指定位置
        return (x, y)
    
    # 找人口最多的点
    filtered_coords = coords[mask]
    filtered_pops = pops[mask]
    best_idx = np.argmax(filtered_pops)
    
    return (filtered_coords[best_idx][0], filtered_coords[best_idx][1])

# ═══════════════════════════════════════════════════════════════
# 第4步: 计算指标
# ═══════════════════════════════════════════════════════════════
def calc_travel_time_from_station(start_station, gdf_sta, lines):
    """
    计算从起始站到所有其他站的旅行时间
    考虑：停站30s, 时速80km/h, 换乘5min
    同线不需要换乘，跨线才需要换乘
    """
    sta_coords = {s['station_id']: (s.geometry.x, s.geometry.y) for _, s in gdf_sta.iterrows()}
    sta_ids = list(sta_coords.keys())
    
    # 每条线路的站集合
    line_station_sets = {line['name']: set(line['stations']) for line in lines}
    
    # 找出起始站所在的线路
    start_lines = []
    for line_name, sta_set in line_station_sets.items():
        if start_station in sta_set:
            start_lines.append(line_name)
    
    travel_times = {}
    
    for target_id in sta_ids:
        if target_id == start_station:
            travel_times[target_id] = 0
            continue
        
        start_coord = sta_coords[start_station]
        target_coord = sta_coords[target_id]
        d = dist_m(start_coord, target_coord)
        
        # 检查目标站是否和起始站在同一条线上
        same_line = False
        for line_name in start_lines:
            if target_id in line_station_sets[line_name]:
                same_line = True
                break
        
        # 计算中间站数
        n_mid = max(0, int(d / 1000) - 1)  # 站距1km
        
        # 旅行时间
        if same_line:
            # 同线：乘车 + 停站（不需要换乘）
            t = d/V_MAX + n_mid*(T_STOP_S+T_ACCEL_S)
        else:
            # 跨线：乘车 + 停站 + 换乘(5min)
            t = d/V_MAX + n_mid*(T_STOP_S+T_ACCEL_S) + T_TRANSFER_S
        
        travel_times[target_id] = t
    
    return travel_times

def draw_isochrones(gdf_sta, lines, gdf_pop, G_road):
    """
    绘制等时圈：从指定站出发，按5min间隔
    """
    print("第6步: 等时圈分析...")
    
    # 选择起始站：S1（东西线西端）
    transfer_station_id = 'S1'
    
    print(f"  起始站: {transfer_station_id}")
    
    # 计算旅行时间
    travel_times = calc_travel_time_from_station(transfer_station_id, gdf_sta, lines)
    
    # 按5min间隔分组
    time_intervals = [5, 10, 15, 20, 25, 30]  # 分钟
    time_colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b']
    
    # 绘制等时圈
    west, south, east, north = BBOX
    fig, ax = plt.subplots(1, 1, figsize=(12, 10))
    
    # 底图：人口分布
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#cccccc', edge_linewidth=0.3, show=False)
    gdf_pop.plot(ax=ax, column='pop', cmap='YlOrRd', markersize=2, alpha=0.5, legend=True)
    
    # 绘制线路
    colors = ['#1f77b4', '#ff7f0e']
    for k, line in enumerate(lines):
        ax.plot([p[0] for p in line['coords']], [p[1] for p in line['coords']], 
                '-', color=colors[k % 2], linewidth=3, alpha=0.8, label=line['name'])
    
    # 绘制车站
    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=50, zorder=5)
    
    # 标记起始站
    start_sta = gdf_sta[gdf_sta['station_id'] == transfer_station_id].iloc[0]
    ax.scatter(start_sta.geometry.x, start_sta.geometry.y, s=200, c='red', 
               edgecolors='black', linewidths=2, zorder=6, marker='*')
    ax.annotate(f'起点: {transfer_station_id}', xy=(start_sta.geometry.x, start_sta.geometry.y),
                fontsize=10, ha='center', va='bottom', xytext=(0, 15), textcoords='offset points',
                fontweight='bold', color='red')
    
    # 绘制等时圈（用圆圈表示）
    for i, t_min in enumerate(time_intervals):
        t_sec = t_min * 60
        # 计算在t秒内能到达的距离（简化：不考虑停站和换乘）
        d_m = V_MAX * t_sec  # 米
        d_deg = d_m / 111000  # 转换为度（粗略）
        
        circle = plt.Circle((start_sta.geometry.x, start_sta.geometry.y), d_deg, 
                            color=time_colors[i], alpha=0.1, linewidth=2, 
                            edgecolor=time_colors[i], linestyle='--')
        ax.add_patch(circle)
        
        # 标记时间
        ax.annotate(f'{t_min}min', 
                    xy=(start_sta.geometry.x + d_deg * 0.707, start_sta.geometry.y + d_deg * 0.707),
                    fontsize=9, ha='center', va='center', color=time_colors[i], fontweight='bold')
    
    # 标记每个站的旅行时间
    for _, sta in gdf_sta.iterrows():
        sta_id = sta['station_id']
        t = travel_times.get(sta_id, 0)
        t_min = t / 60
        
        # 找最近的时间间隔
        nearest_interval = min(time_intervals, key=lambda x: abs(x - t_min))
        color_idx = time_intervals.index(nearest_interval)
        
        ax.annotate(f'{t_min:.0f}min', xy=(sta.geometry.x, sta.geometry.y),
                    fontsize=8, ha='center', va='center', 
                    color=time_colors[color_idx], fontweight='bold',
                    xytext=(0, -15), textcoords='offset points')
    
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.set_title(f'等时圈分析 (从{transfer_station_id}出发, 每5min一圈)\n'
                 f'时速80km/h | 停站30s | 换乘5min', fontsize=13)
    ax.legend(fontsize=10)
    
    plt.tight_layout()
    plt.savefig(f'{OUTPUT}/isochrones.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  已保存: {OUTPUT}/isochrones.png")
    
    # 输出旅行时间统计
    print(f"\n  旅行时间统计 (从{transfer_station_id}出发):")
    for t_min in time_intervals:
        count = sum(1 for t in travel_times.values() if t/60 <= t_min)
        print(f"    {t_min}min内可达: {count}站")

def calc_metrics(gdf_sta, lines, gdf_pop, cover_sets, coords, pops):
    """计算4个指标"""
    print("第4步: 计算指标...")

    n_stations = len(gdf_sta)

    total_length = 0
    for line in lines:
        for i in range(len(line['coords'])-1):
            total_length += dist_m(line['coords'][i], line['coords'][i+1])

    covered = set()
    for cs in cover_sets:
        covered |= cs
    coverage = sum(pops[j] for j in covered) / pops.sum()

    # 平均旅速 (含换乘惩罚)
    sta_coords = [(s.geometry.x, s.geometry.y) for _, s in gdf_sta.iterrows()]
    speeds = []
    for i in range(min(len(sta_coords), 20)):
        for j in range(i+1, min(len(sta_coords), 20)):
            d = dist_m(sta_coords[i], sta_coords[j])
            if d < 100: continue
            
            # 检查是否需要换乘 (简化: 如果两点不在同一条线上需要换乘)
            same_line = False
            for line in lines:
                if (sta_coords[i] in line['coords'] and 
                    sta_coords[j] in line['coords']):
                    same_line = True
                    break
            
            n_mid = max(0, int(d / STA_INTERVAL) - 1)
            if same_line:
                t = T_ACCESS_S + d/V_MAX + n_mid*(T_STOP_S+T_ACCEL_S) + T_EGRESS_S
            else:
                t = T_ACCESS_S + d/V_MAX + n_mid*(T_STOP_S+T_ACCEL_S) + T_TRANSFER_S + T_EGRESS_S
            
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
# 第5步: 可视化
# ═══════════════════════════════════════════════════════════════
def visualize(gdf_pop, gdf_sta, lines, G_road, metrics):
    print("第5步: 可视化...")
    west, south, east, north = BBOX
    fig, axes = plt.subplots(2, 2, figsize=(16, 14))
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']

    # 左上: 人口分布
    ax = axes[0,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#cccccc', edge_linewidth=0.3, show=False)
    gdf_pop.plot(ax=ax, column='pop', cmap='YlOrRd', markersize=3, alpha=0.7, legend=True)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('人口分布', fontsize=13)

    # 右上: 线路设计
    ax = axes[0,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=2, alpha=0.3)
    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        ax.plot([p[0] for p in line['coords']], [p[1] for p in line['coords']], 
                '-', color=c, linewidth=4, alpha=0.8, label=line['name'])
    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=60, zorder=5)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('线路优先设计 (东西线+南北线)', fontsize=13)
    ax.legend(fontsize=10)

    # 左下: 站距分析
    ax = axes[1,0]
    # 计算每条线的站距
    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        dists = []
        for i in range(len(line['coords'])-1):
            d = dist_m(line['coords'][i], line['coords'][i+1])
            dists.append(d)
        if dists:
            ax.bar(range(len(dists)), dists, color=c, alpha=0.6, label=f'{line['name']} (平均{np.mean(dists):.0f}m)')
    ax.axhline(y=STA_INTERVAL, color='red', linestyle='--', label=f'目标站距 {STA_INTERVAL}m')
    ax.set_xlabel('站序号')
    ax.set_ylabel('站距 (m)')
    ax.set_title('站距分析', fontsize=13)
    ax.legend()

    # 右下: 最终方案
    ax = axes[1,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#dddddd', edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=1, alpha=0.1)
    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        ax.plot([p[0] for p in line['coords']], [p[1] for p in line['coords']], 
                '-', color=c, linewidth=4, alpha=0.85)
    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=70, zorder=5)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    title = (f'最终方案\n'
             f'建站: {metrics["n_stations"]}站 | 里程: {metrics["total_length_km"]:.1f}km\n'
             f'覆盖: {metrics["coverage"]*100:.1f}% | 旅速: {metrics["avg_speed_kmh"]:.1f}km/h')
    ax.set_title(title, fontsize=12)

    plt.tight_layout()
    plt.savefig(f'{OUTPUT}/metro_plan_v11.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  已保存: {OUTPUT}/metro_plan_v11.png")

def export_geojson(gdf_sta, lines):
    fc = {"type":"FeatureCollection","features":[]}
    for _, s in gdf_sta.iterrows():
        fc['features'].append({"type":"Feature","geometry":{"type":"Point","coordinates":[s.geometry.x,s.geometry.y]},"properties":{"id":s['station_id']}})
    with open(f'{OUTPUT}/stations.geojson','w') as f: json.dump(fc,f,indent=2)
    fc2 = {"type":"FeatureCollection","features":[]}
    for line in lines:
        fc2['features'].append({"type":"Feature","geometry":{"type":"LineString","coordinates":[(p[0],p[1]) for p in line['coords']]},"properties":{"name":line['name']}})
    with open(f'{OUTPUT}/metro_lines.geojson','w') as f: json.dump(fc2,f,indent=2)
    print(f"  已导出到 {OUTPUT}")

# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════
def main():
    print("="*60)
    print("地铁规划 v11 — 线路优先，摒弃聚类")
    print("="*60)

    buildings, G_road = get_data()
    gdf_pop = make_population(buildings)
    gdf_sta, lines, cover_sets, coords, pops = line_first_design(gdf_pop)
    metrics = calc_metrics(gdf_sta, lines, gdf_pop, cover_sets, coords, pops)
    export_geojson(gdf_sta, lines)
    visualize(gdf_pop, gdf_sta, lines, G_road, metrics)
    draw_isochrones(gdf_sta, lines, gdf_pop, G_road)

    print("\n✓ 完成!")

if __name__ == '__main__':
    main()
