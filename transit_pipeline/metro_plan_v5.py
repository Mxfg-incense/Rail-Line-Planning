#!/usr/bin/env python3
"""
地铁规划 v5.1 — 转弯半径约束修复版
核心改动: 对于R<1000m的转弯, 删除该中间点, 用直线连接
这样路径更直, 转弯更平缓
"""

import os, json, numpy as np, geopandas as gpd
import osmnx as ox
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
GRID_SIZE_M  = 1000
COVERAGE_R   = 800
MIN_STA_DIST = 1000
R_MIN_M      = 1000   # 最小转弯半径 (米)

V_MAX_KMH    = 80
T_STOP_S     = 30
T_ACCEL_S    = 20
T_TRANSFER_S = 5 * 60
T_ACCESS_S   = 5 * 60
T_EGRESS_S   = 5 * 60
V_MAX = V_MAX_KMH / 3.6

# ── 坐标转换辅助 ─────────────────────────────────────────────
LAT_MID = (BBOX[1] + BBOX[3]) / 2
M_LON = 111_000 * np.cos(np.radians(LAT_MID))
M_LAT = 111_000

def to_m(dx_deg, dy_deg):
    return dx_deg * M_LON, dy_deg * M_LAT

def dist_m(p1, p2):
    dx, dy = to_m(p1[0]-p2[0], p1[1]-p2[1])
    return np.sqrt(dx**2 + dy**2)

# ═══════════════════════════════════════════════════════════════
# 转弯半径计算
# ═══════════════════════════════════════════════════════════════
def turn_radius(A, B, C):
    """计算A→B→C的转弯半径(米)"""
    BA = np.array(to_m(A[0]-B[0], A[1]-B[1]))
    BC = np.array(to_m(C[0]-B[0], C[1]-B[1]))
    len_BA = np.linalg.norm(BA)
    len_BC = np.linalg.norm(BC)
    if len_BA < 1 or len_BC < 1:
        return float('inf')
    cos_angle = np.clip(np.dot(BA, BC) / (len_BA * len_BC), -1, 1)
    angle_ABC = np.arccos(cos_angle)
    turn_angle = np.pi - angle_ABC
    if turn_angle < 0.01:
        return float('inf')
    d_avg = (len_BA + len_BC) / 2
    R = d_avg / (2 * np.sin(turn_angle / 2))
    return R

def smooth_line_by_removing(coords, r_min=R_MIN_M, max_iter=5):
    """
    对于R<r_min的转弯, 删除该中间点, 用直线连接前后两点
    迭代直到所有转弯满足约束
    """
    path = list(coords)
    for iteration in range(max_iter):
        if len(path) < 3:
            break
        new_path = [path[0]]
        removed = 0
        i = 1
        while i < len(path) - 1:
            A = new_path[-1]
            B = path[i]
            C = path[i+1]
            R = turn_radius(A, B, C)
            if R < r_min:
                # 删除B点, 直接连接A→C
                removed += 1
                i += 1  # 跳过B
            else:
                new_path.append(B)
                i += 1
        new_path.append(path[-1])
        path = new_path
        if removed == 0:
            break
    return path

# ═══════════════════════════════════════════════════════════════
# 旅速计算
# ═══════════════════════════════════════════════════════════════
def avg_travel_speed(gdf_sta, metro_lines, gdf_pop):
    sta_ids = list(gdf_sta['station_id'])
    sta_xy = {s['station_id']: (s.geometry.x, s.geometry.y) for _, s in gdf_sta.iterrows()}
    n = len(sta_ids)
    tt = np.full((n, n), np.inf)
    sta_line = {}
    for line in metro_lines:
        for sid in line['stations']:
            sta_line[sid] = line['name']
    for i in range(n):
        for j in range(i+1, n):
            si, sj = sta_ids[i], sta_ids[j]
            d = dist_m(sta_xy[si], sta_xy[sj])
            same = (sta_line.get(si) == sta_line.get(sj))
            if same:
                n_mid = max(0, int(d / 1500) - 1)
                t = T_ACCESS_S + d/V_MAX + n_mid*(T_STOP_S+T_ACCEL_S) + T_EGRESS_S
            else:
                d2 = d/2
                n_mid = max(0, int(d2 / 1500) - 1)
                t = T_ACCESS_S + d2/V_MAX + n_mid*(T_STOP_S+T_ACCEL_S) + T_TRANSFER_S + d2/V_MAX + n_mid*(T_STOP_S+T_ACCEL_S) + T_EGRESS_S
            tt[i,j] = tt[j,i] = t
    speeds = []
    for i in range(n):
        for j in range(i+1, n):
            if tt[i,j] < np.inf and tt[i,j] > 0:
                d = dist_m(sta_xy[sta_ids[i]], sta_xy[sta_ids[j]])
                speeds.append(d / tt[i,j] * 3.6)
    return (np.mean(speeds), speeds) if speeds else (0, [])

# ═══════════════════════════════════════════════════════════════
# 数据获取
# ═══════════════════════════════════════════════════════════════
def get_data():
    print("第1步: 获取OSM数据...")
    buildings = ox.features_from_bbox(bbox=BBOX, tags={'building': True})
    G_road = ox.graph_from_bbox(bbox=BBOX, network_type='drive')
    print(f"  建筑物: {len(buildings)}, 路网: {len(G_road.nodes)}节点")
    return buildings, G_road

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
    print(f"  总人口: {gdf['pop'].sum()}, 点数: {len(gdf)}")
    return gdf

def cluster_population(gdf_pop):
    print("第3步: 1km聚类...")
    west, south, east, north = BBOX
    gdx = GRID_SIZE_M / M_LON; gdy = GRID_SIZE_M / M_LAT
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
            cc = coords[m]; pp = pops[m]
            s = pp.sum()
            wx = np.average(cc[:,0], weights=pp) if s>0 else cc[:,0].mean()
            wy = np.average(cc[:,1], weights=pp) if s>0 else cc[:,1].mean()
            cands.append({'geometry': Point(wx, wy), 'pop': int(s)})
    gdf = gpd.GeoDataFrame(cands, crs='EPSG:4326')
    print(f"  候选: {len(gdf)}")
    return gdf

def greedy_select(gdf_cand, min_dist=MIN_STA_DIST):
    df = gdf_cand.sort_values('pop', ascending=False).reset_index(drop=True)
    sel = []
    for _, row in df.iterrows():
        pt = row.geometry
        ok = True
        for sp in sel:
            d = dist_m((pt.x, pt.y), (sp.x, sp.y))
            if d < min_dist: ok=False; break
        if ok: sel.append(pt)
    gdf = gpd.GeoDataFrame([{'geometry': p, 'station_id': f'S{i+1}'} for i, p in enumerate(sel)], crs='EPSG:4326')
    return gdf

def build_lines(gdf_sta):
    df = gdf_sta.copy()
    df['lon']=df.geometry.x; df['lat']=df.geometry.y
    mid = df['lat'].median()
    n = df[df['lat']>=mid].sort_values('lon')
    s = df[df['lat']<mid].sort_values('lon')
    lines = []
    if len(n)>=2: lines.append({'name':'Line1 (北线)','coords':[(g.x,g.y) for g in n.geometry],'stations':n['station_id'].tolist()})
    if len(s)>=2: lines.append({'name':'Line2 (南线)','coords':[(g.x,g.y) for g in s.geometry],'stations':s['station_id'].tolist()})
    if len(n)>=1 and len(s)>=1:
        best_d,best_p = 1e9,None
        for _,ni in n.iterrows():
            for _,si in s.iterrows():
                d = dist_m((ni.geometry.x,ni.geometry.y),(si.geometry.x,si.geometry.y))
                if d<best_d: best_d,best_p=d,(ni,si)
        if best_p and best_d<3000:
            lines.append({'name':'换乘连接','coords':[(best_p[0].geometry.x,best_p[0].geometry.y),(best_p[1].geometry.x,best_p[1].geometry.y)],'stations':[best_p[0]['station_id'],best_p[1]['station_id']]})
    return lines

def smooth_metro_lines(metro_lines):
    """对所有线路做转弯半径平滑"""
    print("转弯半径平滑 (删除违规中间点)...")
    smoothed = []
    for line in metro_lines:
        coords = line['coords']
        if len(coords) < 3:
            smoothed.append({**line, 'smooth_coords': coords})
            continue

        smooth_coords = smooth_line_by_removing(coords)
        n_removed = len(coords) - len(smooth_coords)

        # 验证
        violations = 0
        for i in range(1, len(smooth_coords) - 1):
            R = turn_radius(smooth_coords[i-1], smooth_coords[i], smooth_coords[i+1])
            if R < R_MIN_M:
                violations += 1

        print(f"  {line['name']}: {len(coords)}站 → {len(smooth_coords)}站, "
              f"删除{n_removed}个违规点, 剩余违规{violations}处")
        smoothed.append({**line, 'smooth_coords': smooth_coords})
    return smoothed

# ═══════════════════════════════════════════════════════════════
# 可视化
# ═══════════════════════════════════════════════════════════════
def visualize(gdf_pop, gdf_cand, gdf_sta, smooth_lines, G_road, metro_lines_orig):
    print("可视化...")
    west, south, east, north = BBOX
    fig, axes = plt.subplots(2, 2, figsize=(16, 14))

    # 左上: 人口 + 路网
    ax = axes[0,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#cccccc', edge_linewidth=0.3, show=False)
    gdf_pop.plot(ax=ax, column='pop', cmap='YlOrRd', markersize=3, alpha=0.7, legend=True)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('人口分布 + OSM路网', fontsize=13)

    # 右上: 候选 + 最终车站
    ax = axes[0,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.2, show=False)
    gdf_cand.plot(ax=ax, color='lightgreen', markersize=20, alpha=0.5)
    gdf_sta.plot(ax=ax, color='red', markersize=60, edgecolor='black', linewidth=0.8, zorder=5)
    for _, s in gdf_sta.iterrows():
        ax.annotate(s['station_id'], xy=(s.geometry.x, s.geometry.y), fontsize=7,
                    ha='center', va='bottom', xytext=(0,5), textcoords='offset points')
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title(f'候选(绿) → 最终车站(红) {len(gdf_sta)}个', fontsize=13)

    # 左下: 原始 vs 平滑线路
    ax = axes[1,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.15, show=False)
    colors = {'Line1 (北线)':'#1f77b4','Line2 (南线)':'#ff7f0e','换乘连接':'#2ca02c'}

    for line in smooth_lines:
        c = colors.get(line['name'], 'gray')
        # 原始直线 (虚线)
        oc = line['coords']
        ax.plot([p[0] for p in oc], [p[1] for p in oc], '--', color=c, linewidth=1.5, alpha=0.5, label=f"{line['name']} (原始{len(oc)}站)")
        # 平滑后 (实线)
        sc = line['smooth_coords']
        ax.plot([p[0] for p in sc], [p[1] for p in sc], '-', color=c, linewidth=3, alpha=0.9, label=f"{line['name']} (平滑{len(sc)}站)")

    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=50, zorder=5)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title(f'转弯半径约束 R≥{R_MIN_M}m (虚线=原始, 实线=平滑)', fontsize=13)
    ax.legend(fontsize=7, loc='upper right')

    # 右下: 最终方案
    ax = axes[1,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#dddddd', edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=1, alpha=0.1)
    for line in smooth_lines:
        c = colors.get(line['name'], 'gray')
        sc = line['smooth_coords']
        ax.plot([p[0] for p in sc], [p[1] for p in sc], '-', color=c, linewidth=3, alpha=0.85)
    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=60, zorder=5)
    for _, s in gdf_sta.iterrows():
        ax.annotate(s['station_id'], xy=(s.geometry.x, s.geometry.y), fontsize=8,
                    ha='center', va='bottom', xytext=(0,5), textcoords='offset points', fontweight='bold')
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('最终地铁方案 + OSM路网', fontsize=13)

    plt.tight_layout()
    plt.savefig(f'{OUTPUT}/metro_plan_v5.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  已保存: {OUTPUT}/metro_plan_v5.png")

# ═══════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════
def export_geojson(gdf_sta, smooth_lines):
    fc = {"type":"FeatureCollection","features":[]}
    for _, s in gdf_sta.iterrows():
        fc['features'].append({"type":"Feature","geometry":{"type":"Point","coordinates":[s.geometry.x,s.geometry.y]},"properties":{"id":s['station_id']}})
    with open(f'{OUTPUT}/stations.geojson','w') as f: json.dump(fc,f,indent=2)
    fc2 = {"type":"FeatureCollection","features":[]}
    for line in smooth_lines:
        fc2['features'].append({"type":"Feature","geometry":{"type":"LineString","coordinates":line['smooth_coords']},"properties":{"name":line['name']}})
    with open(f'{OUTPUT}/metro_lines_smooth.geojson','w') as f: json.dump(fc2,f,indent=2)
    print(f"  已导出到 {OUTPUT}")

# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════
def main():
    print("="*60)
    print("地铁规划 v5.1 — 转弯半径约束 (修复版)")
    print("策略: 删除R<1000m的违规中间点")
    print("="*60)

    buildings, G_road = get_data()
    gdf_pop = make_population(buildings)
    gdf_cand = cluster_population(gdf_pop)

    print("\n第4步: 选站 (站距≥1000m)...")
    gdf_sta = greedy_select(gdf_cand)
    print(f"  车站: {len(gdf_sta)}")

    print("\n第5步: 生成线路...")
    metro_lines = build_lines(gdf_sta)
    for l in metro_lines:
        print(f"  {l['name']}: {len(l['stations'])}站")

    print("\n第6步: 转弯半径平滑...")
    smooth_lines = smooth_metro_lines(metro_lines)

    print("\n第7步: 计算旅速...")
    avg_v, speeds = avg_travel_speed(gdf_sta, smooth_lines, gdf_pop)
    print(f"  平均旅速: {avg_v:.1f} km/h")
    print(f"  中位数: {np.median(speeds):.1f}, 最小: {np.min(speeds):.1f}, 最大: {np.max(speeds):.1f}")

    export_geojson(gdf_sta, smooth_lines)
    visualize(gdf_pop, gdf_cand, gdf_sta, smooth_lines, G_road, metro_lines)

    print("\n✓ 完成!")

if __name__ == '__main__':
    main()
