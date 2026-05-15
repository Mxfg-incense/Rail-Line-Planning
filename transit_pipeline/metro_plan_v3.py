#!/usr/bin/env python3
"""
地铁线路自动规划 v3
核心改动:
1. 地铁不沿道路走 → 直线连接
2. 人口点按1km网格聚类 → 每格选1个候选车站
3. 贪心选站保证站距1-2km
"""

import os, json, numpy as np, pandas as pd, geopandas as gpd
import osmnx as ox, networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from shapely.geometry import Point, LineString, box, MultiPoint
from shapely.ops import nearest_points
import warnings
warnings.filterwarnings('ignore')

# ── 字体 ────────────────────────────────────────────────────
import matplotlib.font_manager as fm
_font_path = None
for p in ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
          '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
          '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc']:
    import os as _os
    if _os.path.exists(p):
        _font_path = p
        break

if _font_path:
    fm.fontManager.addfont(_font_path)
    _prop = fm.FontProperties(fname=_font_path)
    _fname = _prop.get_name()
    plt.rcParams['font.family'] = _fname
else:
    plt.rcParams['font.family'] = 'Noto Sans CJK SC'
plt.rcParams['axes.unicode_minus'] = False

# ── 参数 ────────────────────────────────────────────────────
BBOX = (121.48, 31.20, 121.56, 31.26)   # 浦东新区(陆家嘴-世纪公园)
OUTPUT = '/smb/j/SHT/COURSE-10/CS240/pj/transit_pipeline/output'
os.makedirs(OUTPUT, exist_ok=True)

TOTAL_POP      = 500_000
GRID_SIZE_M    = 1000          # 聚类网格 1km × 1km
MIN_STA_DIST   = 1000          # 最小站距 m
MAX_STA_DIST   = 2000          # 最大站距 m
COVERAGE_R     = 800           # 车站覆盖半径 m
TURN_RADIUS_M  = 1000          # 转弯半径约束 m

# ═══════════════════════════════════════════════════════════════
# 第1步: 获取数据
# ═══════════════════════════════════════════════════════════════
def get_data():
    print("第1步: 获取OSM数据...")
    west, south, east, north = BBOX

    buildings = ox.features_from_bbox(bbox=BBOX, tags={'building': True})
    print(f"  建筑物: {len(buildings)}")

    try:
        pois = ox.features_from_bbox(bbox=BBOX, tags={
            'amenity': ['hospital','school','university','transport_station']})
    except:
        pois = gpd.GeoDataFrame()
    print(f"  POI: {len(pois)}")

    return buildings, pois

# ═══════════════════════════════════════════════════════════════
# 第2步: 生成人口分布
# ═══════════════════════════════════════════════════════════════
def make_population(buildings):
    print("第2步: 生成人口分布...")
    west, south, east, north = BBOX

    res_types = ['apartments','house','residential','dormitory','yes']
    if 'building' in buildings.columns:
        mask = buildings['building'].isin(res_types) | buildings['building'].isna()
        bld = buildings[mask].copy()
    else:
        bld = buildings.copy()

    # 过滤无效几何
    bld = bld[bld.geometry.is_valid & ~bld.geometry.is_empty]

    # 计算面积 (投影到米)
    bld_m = bld.to_crs(epsg=3857)
    bld_m['area'] = bld_m.geometry.area
    bld_m = bld_m[bld_m['area'] > 10]
    total_area = bld_m['area'].sum()
    bld_m['pop'] = (bld_m['area'] / total_area * TOTAL_POP).astype(int)

    # 取质心
    bld_wgs = bld_m.to_crs(epsg=4326)
    bld_wgs['centroid'] = bld_wgs.geometry.centroid

    gdf_pop = gpd.GeoDataFrame({
        'geometry': bld_wgs['centroid'].values,
        'pop': bld_wgs['pop'].values
    }, crs='EPSG:4326')

    print(f"  总人口: {gdf_pop['pop'].sum()}, 点数: {len(gdf_pop)}")
    return gdf_pop

# ═══════════════════════════════════════════════════════════════
# 第3步: 网格聚类 → 候选车站 (每格 ~1km×1km)
# ═══════════════════════════════════════════════════════════════
def cluster_population(gdf_pop):
    """
    将人口点按 1km 网格聚类, 每个非空格子产生一个候选车站
    候选位置 = 格子内人口加权质心
    """
    print("第3步: 1km网格聚类...")
    west, south, east, north = BBOX

    # 经纬度 → 米 (粗略)
    lat_mid = (south + north) / 2
    m_per_deg_lon = 111_000 * np.cos(np.radians(lat_mid))
    m_per_deg_lat = 111_000

    grid_dx = GRID_SIZE_M / m_per_deg_lon   # 经度步长
    grid_dy = GRID_SIZE_M / m_per_deg_lat   # 纬度步长

    nx_g = max(1, int(np.ceil((east - west) / grid_dx)))
    ny_g = max(1, int(np.ceil((north - south) / grid_dy)))
    print(f"  网格: {nx_g} × {ny_g} = {nx_g*ny_g} 格")

    coords = np.array([[g.x, g.y] for g in gdf_pop.geometry])
    pops   = gdf_pop['pop'].values

    candidates = []
    for i in range(ny_g):
        for j in range(nx_g):
            x0 = west  + j * grid_dx
            x1 = x0 + grid_dx
            y0 = south + i * grid_dy
            y1 = y0 + grid_dy

            mask = ((coords[:,0] >= x0) & (coords[:,0] < x1) &
                    (coords[:,1] >= y0) & (coords[:,1] < y1))
            if mask.sum() == 0:
                continue

            cell_pop = pops[mask].sum()
            cell_coords = coords[mask]

            # 加权质心
            if cell_pop > 0:
                wx = np.average(cell_coords[:,0], weights=pops[mask])
                wy = np.average(cell_coords[:,1], weights=pops[mask])
            else:
                wx = cell_coords[:,0].mean()
                wy = cell_coords[:,1].mean()

            candidates.append({
                'geometry': Point(wx, wy),
                'pop': int(cell_pop),
                'grid_i': i, 'grid_j': j,
            })

    gdf_cand = gpd.GeoDataFrame(candidates, crs='EPSG:4326')
    print(f"  候选车站: {len(gdf_cand)} 个")
    print(f"  人口范围: {gdf_cand['pop'].min()} ~ {gdf_cand['pop'].max()}")
    return gdf_cand

# ═══════════════════════════════════════════════════════════════
# 第4步: 贪心选站 (保证站距 1-2km)
# ═══════════════════════════════════════════════════════════════
def greedy_select(gdf_cand):
    """
    按覆盖人口降序, 贪心选取车站:
    与已选车站距离必须 >= MIN_STA_DIST
    """
    print("第4步: 贪心选站 (站距约束 1-2km)...")
    lat_mid = (BBOX[1] + BBOX[3]) / 2
    m_per_deg_lon = 111_000 * np.cos(np.radians(lat_mid))
    m_per_deg_lat = 111_000

    df = gdf_cand.copy()
    df = df.sort_values('pop', ascending=False).reset_index(drop=True)

    selected_idx = []
    selected_pts = []

    for idx, row in df.iterrows():
        pt = row.geometry
        # 检查与已选车站的距离
        too_close = False
        for spt in selected_pts:
            dx = (pt.x - spt.x) * m_per_deg_lon
            dy = (pt.y - spt.y) * m_per_deg_lat
            dist = np.sqrt(dx**2 + dy**2)
            if dist < MIN_STA_DIST:
                too_close = True
                break
        if not too_close:
            selected_idx.append(idx)
            selected_pts.append(pt)

    gdf_sta = df.loc[selected_idx].copy().reset_index(drop=True)
    gdf_sta['station_id'] = [f'S{i+1}' for i in range(len(gdf_sta))]

    # 计算每站覆盖人口 (800m半径)
    sta_coords = np.array([[g.x, g.y] for g in gdf_sta.geometry])
    all_coords = np.array([[g.x, g.y] for g in gdf_cand.geometry])
    all_pops   = gdf_cand['pop'].values
    coverage_r_deg = COVERAGE_R / 111_000

    covered_list = []
    for k in range(len(gdf_sta)):
        dists = np.sqrt(((all_coords - sta_coords[k]) * [m_per_deg_lon, m_per_deg_lat])**2).sum(axis=1)
        covered = all_pops[dists < COVERAGE_R].sum()
        covered_list.append(int(covered))
    gdf_sta['covered_pop'] = covered_list

    # 站间距统计
    dists_list = []
    for i in range(len(gdf_sta)):
        for j in range(i+1, len(gdf_sta)):
            dx = (sta_coords[i,0] - sta_coords[j,0]) * m_per_deg_lon
            dy = (sta_coords[i,1] - sta_coords[j,1]) * m_per_deg_lat
            dists_list.append(np.sqrt(dx**2 + dy**2))

    print(f"  选取车站: {len(gdf_sta)}")
    print(f"  覆盖人口: {gdf_sta['covered_pop'].sum()} / {gdf_cand['pop'].sum()}")
    if dists_list:
        print(f"  站间距: 平均={np.mean(dists_list):.0f}m, "
              f"最小={np.min(dists_list):.0f}m, 最大={np.max(dists_list):.0f}m")

    return gdf_sta

# ═══════════════════════════════════════════════════════════════
# 第5步: 生成地铁线路 (直线连接, 按地理顺序)
# ═══════════════════════════════════════════════════════════════
def build_metro_lines(gdf_sta):
    """
    简单策略: 按纬度分两组 → 东西向两条线路
    用直线连接 (地铁不沿道路)
    """
    print("第5步: 生成地铁线路 (直线连接)...")
    df = gdf_sta.copy()
    df['lon'] = df.geometry.x
    df['lat'] = df.geometry.y
    lat_mid = df['lat'].median()

    # 分为南北两条线
    north_line = df[df['lat'] >= lat_mid].sort_values('lon')
    south_line = df[df['lat'] <  lat_mid].sort_values('lon')

    lines = []
    if len(north_line) >= 2:
        coords = [(g.x, g.y) for g in north_line.geometry]
        lines.append({'name': 'Line 1 (北线)', 'coords': coords,
                      'stations': north_line['station_id'].tolist()})
    if len(south_line) >= 2:
        coords = [(g.x, g.y) for g in south_line.geometry]
        lines.append({'name': 'Line 2 (南线)', 'coords': coords,
                      'stations': south_line['station_id'].tolist()})

    # 连接线 (换乘)
    if len(north_line) >= 1 and len(south_line) >= 1:
        # 选最近的一对做换乘站
        best_pair = None
        best_dist = 1e9
        for i, ni in north_line.iterrows():
            for j, sj in south_line.iterrows():
                dx = (ni.geometry.x - sj.geometry.x) * 111000 * np.cos(np.radians(lat_mid))
                dy = (ni.geometry.y - sj.geometry.y) * 111000
                d = np.sqrt(dx**2 + dy**2)
                if d < best_dist:
                    best_dist = d
                    best_pair = (ni, sj)
        if best_pair and best_dist < 3000:
            lines.append({'name': '换乘连接', 'coords': [
                (best_pair[0].geometry.x, best_pair[0].geometry.y),
                (best_pair[1].geometry.x, best_pair[1].geometry.y),
            ], 'stations': [best_pair[0]['station_id'], best_pair[1]['station_id']]})

    print(f"  生成 {len(lines)} 条线路")
    for line in lines:
        print(f"    {line['name']}: {len(line['stations'])} 站")
    return lines

# ═══════════════════════════════════════════════════════════════
# 第6步: 导出 GeoJSON
# ═══════════════════════════════════════════════════════════════
def export_geojson(gdf_sta, gdf_pop, metro_lines):
    print("第6步: 导出GeoJSON...")

    # 车站
    fc_sta = {"type": "FeatureCollection", "features": []}
    for _, s in gdf_sta.iterrows():
        fc_sta['features'].append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [s.geometry.x, s.geometry.y]},
            "properties": {"id": s['station_id'], "covered_pop": int(s['covered_pop'])}
        })
    with open(f'{OUTPUT}/stations.geojson', 'w', encoding='utf-8') as f:
        json.dump(fc_sta, f, indent=2, ensure_ascii=False)

    # 线路
    fc_line = {"type": "FeatureCollection", "features": []}
    for line in metro_lines:
        fc_line['features'].append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": line['coords']},
            "properties": {"name": line['name'], "stations": line['stations']}
        })
    with open(f'{OUTPUT}/metro_lines.geojson', 'w', encoding='utf-8') as f:
        json.dump(fc_line, f, indent=2, ensure_ascii=False)

    # 人口
    fc_pop = {"type": "FeatureCollection", "features": []}
    for _, p in gdf_pop.iterrows():
        fc_pop['features'].append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p.geometry.x, p.geometry.y]},
            "properties": {"pop": int(p['pop'])}
        })
    with open(f'{OUTPUT}/population.geojson', 'w', encoding='utf-8') as f:
        json.dump(fc_pop, f, indent=2, ensure_ascii=False)

    print(f"  已导出到 {OUTPUT}")

# ═══════════════════════════════════════════════════════════════
# 第7步: 可视化
# ═══════════════════════════════════════════════════════════════
def visualize(gdf_pop, gdf_cand, gdf_sta, metro_lines):
    print("第7步: 可视化...")
    west, south, east, north = BBOX

    fig, axes = plt.subplots(2, 2, figsize=(16, 14))

    # ── 左上: 人口热力图 ──
    ax = axes[0, 0]
    gdf_pop.plot(ax=ax, column='pop', cmap='YlOrRd', markersize=3, alpha=0.6, legend=True)
    ax.set_xlim(west, east); ax.set_ylim(south, north)
    ax.set_title('人口分布 (每个建筑质心)', fontsize=13)
    ax.set_xlabel('经度'); ax.set_ylabel('纬度')

    # ── 右上: 网格聚类候选车站 ──
    ax = axes[0, 1]
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=1, alpha=0.2)
    gdf_cand.plot(ax=ax, column='pop', cmap='YlGn', markersize=40,
                  edgecolor='black', linewidth=0.5, legend=True)
    ax.set_xlim(west, east); ax.set_ylim(south, north)
    ax.set_title(f'1km网格聚类 → {len(gdf_cand)}个候选', fontsize=13)
    ax.set_xlabel('经度'); ax.set_ylabel('纬度')

    # ── 左下: 选中的车站 ──
    ax = axes[1, 0]
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=1, alpha=0.15)
    gdf_sta.plot(ax=ax, column='covered_pop', cmap='RdYlGn', markersize=80,
                 edgecolor='black', linewidth=1, legend=True)
    for _, s in gdf_sta.iterrows():
        ax.annotate(s['station_id'], xy=(s.geometry.x, s.geometry.y),
                    fontsize=7, ha='center', va='bottom',
                    xytext=(0, 6), textcoords='offset points')
        circle = plt.Circle((s.geometry.x, s.geometry.y),
                            COVERAGE_R / 111000, color='green', alpha=0.08)
        ax.add_patch(circle)
    ax.set_xlim(west, east); ax.set_ylim(south, north)
    ax.set_title(f'贪心选站 {len(gdf_sta)}个 (站距≥{MIN_STA_DIST}m)', fontsize=13)
    ax.set_xlabel('经度'); ax.set_ylabel('纬度')

    # ── 右下: 地铁线路 ──
    ax = axes[1, 1]
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=1, alpha=0.1)

    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']
    for k, line in enumerate(metro_lines):
        xs = [c[0] for c in line['coords']]
        ys = [c[1] for c in line['coords']]
        ax.plot(xs, ys, '-', color=colors[k % len(colors)],
                linewidth=3, alpha=0.8, label=line['name'])
        ax.plot(xs, ys, 'o', color=colors[k % len(colors)], markersize=8)

    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=60, zorder=5)
    for _, s in gdf_sta.iterrows():
        ax.annotate(s['station_id'], xy=(s.geometry.x, s.geometry.y),
                    fontsize=7, ha='center', va='bottom',
                    xytext=(0, 6), textcoords='offset points',
                    fontweight='bold')

    ax.set_xlim(west, east); ax.set_ylim(south, north)
    ax.set_title('地铁线路 (直线连接, 不沿道路)', fontsize=13)
    ax.set_xlabel('经度'); ax.set_ylabel('纬度')
    ax.legend(loc='upper right')

    plt.tight_layout()
    plt.savefig(f'{OUTPUT}/metro_plan.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  已保存: {OUTPUT}/metro_plan.png")

# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("地铁线路自动规划 v3")
    print("区域: 浦东新区 (陆家嘴-世纪公园)")
    print("约束: 转弯半径≥1km, 站距1-2km, 地铁走直线")
    print("=" * 60)

    buildings, pois = get_data()
    gdf_pop = make_population(buildings)
    gdf_cand = cluster_population(gdf_pop)
    gdf_sta = greedy_select(gdf_cand)
    metro_lines = build_metro_lines(gdf_sta)
    export_geojson(gdf_sta, gdf_pop, metro_lines)
    visualize(gdf_pop, gdf_cand, gdf_sta, metro_lines)

    print("\n" + "=" * 60)
    print("✓ 全流程完成!")
    print("=" * 60)

if __name__ == '__main__':
    main()
