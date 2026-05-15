#!/usr/bin/env python3
"""
地铁规划 v6
- 100m网格聚类 (细粒度)
- 站距 1000m-1500m
- 步行可达 800m
- 最多5条线路
- 线路必须连通 (通过换乘站)
- 目标: 最大化覆盖人口
"""

import os, json, numpy as np, geopandas as gpd
import osmnx as ox
import networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from shapely.geometry import Point, LineString
from sklearn.cluster import DBSCAN
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
CLUSTER_R_M  = 100      # 聚类半径 100m
MIN_STA_DIST = 1000     # 最小站距 m
MAX_STA_DIST = 1500     # 最大站距 m
WALK_R_M     = 800      # 步行可达半径 m
MAX_LINES    = 5        # 线路上限
R_MIN_M      = 1000     # 转弯半径 m

# ── 坐标转换 ─────────────────────────────────────────────────
LAT_MID = (BBOX[1] + BBOX[3]) / 2
M_LON = 111_000 * np.cos(np.radians(LAT_MID))
M_LAT = 111_000

def to_m(p1, p2):
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
    print(f"  总人口: {gdf['pop'].sum()}, 点数: {len(gdf)}")
    return gdf

# ═══════════════════════════════════════════════════════════════
# 第3步: 100m网格聚类
# ═══════════════════════════════════════════════════════════════
def cluster_100m(gdf_pop):
    """100m网格聚类, 每格产生一个候选车站"""
    print("第3步: 100m网格聚类...")
    west, south, east, north = BBOX
    gdx = CLUSTER_R_M / M_LON
    gdy = CLUSTER_R_M / M_LAT
    nxg = max(1, int(np.ceil((east-west)/gdx)))
    nyg = max(1, int(np.ceil((north-south)/gdy)))
    print(f"  网格: {nxg}×{nyg} = {nxg*nyg}格")

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
            if s == 0: continue
            wx = np.average(cc[:,0], weights=pp)
            wy = np.average(cc[:,1], weights=pp)
            cands.append({'geometry': Point(wx, wy), 'pop': int(s)})

    gdf = gpd.GeoDataFrame(cands, crs='EPSG:4326')
    print(f"  候选: {len(gdf)}")
    return gdf

# ═══════════════════════════════════════════════════════════════
# 第4步: 贪心选站 (站距1000-1500m)
# ═══════════════════════════════════════════════════════════════
def greedy_select(gdf_cand):
    """按覆盖人口降序贪心选站, 站距≥1000m"""
    print("第4步: 贪心选站 (站距1000-1500m)...")
    df = gdf_cand.sort_values('pop', ascending=False).reset_index(drop=True)
    sel = []
    for _, row in df.iterrows():
        pt = row.geometry
        ok = True
        for sp in sel:
            if to_m((pt.x, pt.y), (sp.x, sp.y)) < MIN_STA_DIST:
                ok = False
                break
        if ok:
            sel.append(pt)

    gdf = gpd.GeoDataFrame([{'geometry': p, 'station_id': f'S{i+1}'} for i, p in enumerate(sel)], crs='EPSG:4326')

    # 计算覆盖人口
    sta_coords = np.array([[g.x, g.y] for g in gdf.geometry])
    all_coords = np.array([[g.x, g.y] for g in gdf_cand.geometry])
    all_pops = gdf_cand['pop'].values
    covered_list = []
    for k in range(len(sta_coords)):
        dx = (all_coords[:,0] - sta_coords[k,0]) * M_LON
        dy = (all_coords[:,1] - sta_coords[k,1]) * M_LAT
        dists = np.sqrt(dx**2 + dy**2)
        covered_list.append(int(all_pops[dists < WALK_R_M].sum()))
    gdf['covered_pop'] = covered_list

    # 站间距统计
    dists_list = []
    for i in range(len(sta_coords)):
        for j in range(i+1, len(sta_coords)):
            d = to_m((sta_coords[i,0], sta_coords[i,1]), (sta_coords[j,0], sta_coords[j,1]))
            dists_list.append(d)

    print(f"  选取: {len(gdf)}站")
    print(f"  覆盖: {gdf['covered_pop'].sum()} / {all_pops.sum()}")
    if dists_list:
        print(f"  站距: 平均={np.mean(dists_list):.0f}m, 最小={np.min(dists_list):.0f}m, 最大={np.max(dists_list):.0f}m")
    return gdf

# ═══════════════════════════════════════════════════════════════
# 第5步: 生成连通线路 (MST + 分线)
# ═══════════════════════════════════════════════════════════════
def build_connected_lines(gdf_sta):
    """
    用最小生成树(MST)连接所有车站, 然后分成最多5条线路
    要求: 所有线路连通 (通过换乘站)
    """
    print("第5步: 生成连通线路 (MST + 分线)...")
    sta_ids = list(gdf_sta['station_id'])
    sta_coords = {s['station_id']: (s.geometry.x, s.geometry.y) for _, s in gdf_sta.iterrows()}
    n = len(sta_ids)

    # 构建完全图
    G_full = nx.Graph()
    for i in range(n):
        for j in range(i+1, n):
            si, sj = sta_ids[i], sta_ids[j]
            d = to_m(sta_coords[si], sta_coords[sj])
            if d <= MAX_STA_DIST * 1.5:  # 只连接距离合理的站
                G_full.add_edge(si, sj, weight=d)

    # MST
    if not nx.is_connected(G_full):
        # 如果图不连通, 添加边使连通
        components = list(nx.connected_components(G_full))
        print(f"  图不连通, {len(components)}个连通分量, 添加连接边...")
        for i in range(len(components)-1):
            comp1 = list(components[i])
            comp2 = list(components[i+1])
            best_d, best_pair = 1e9, None
            for s1 in comp1:
                for s2 in comp2:
                    d = to_m(sta_coords[s1], sta_coords[s2])
                    if d < best_d:
                        best_d, best_pair = d, (s1, s2)
            if best_pair:
                G_full.add_edge(best_pair[0], best_pair[1], weight=best_d)

    mst = nx.minimum_spanning_tree(G_full, weight='weight')
    print(f"  MST: {len(mst.nodes)}节点, {len(mst.edges)}边")

    # 将MST分成最多5条线路
    # 策略: 找MST的最长路径, 然后从中间断开, 重复
    lines = split_mst_to_lines(mst, sta_coords, MAX_LINES)

    # 确保换乘站存在 (两条线路共享的站)
    # 如果没有共享站, 添加最短连接
    for i in range(len(lines)-1):
        shared = set(lines[i]['stations']) & set(lines[i+1]['stations'])
        if not shared:
            # 找最近的一对站连接
            best_d, best_pair = 1e9, None
            for s1 in lines[i]['stations']:
                for s2 in lines[i+1]['stations']:
                    d = to_m(sta_coords[s1], sta_coords[s2])
                    if d < best_d:
                        best_d, best_pair = d, (s1, s2)
            if best_pair:
                # 将换乘站添加到两条线路
                lines[i]['stations'].append(best_pair[1])
                lines[i+1]['stations'].append(best_pair[0])
                print(f"  添加换乘: {best_pair[0]} ↔ {best_pair[1]} (距离{best_d:.0f}m)")

    # 为每条线路生成坐标
    for line in lines:
        line['coords'] = [sta_coords[sid] for sid in line['stations']]

    print(f"  生成 {len(lines)} 条线路:")
    for line in lines:
        print(f"    {line['name']}: {len(line['stations'])}站")

    return lines

def split_mst_to_lines(mst, sta_coords, max_lines):
    """将MST分成最多max_lines条线路"""
    lines = []
    remaining_edges = set(mst.edges())
    remaining_nodes = set(mst.nodes())

    for line_idx in range(max_lines):
        if not remaining_edges:
            break

        # 找最长路径
        try:
            # 用BFS找最远的两个节点
            nodes = list(remaining_nodes)
            if len(nodes) < 2:
                break

            # 从任意节点BFS找最远节点
            start = nodes[0]
            lengths = nx.single_source_shortest_path_length(mst.subgraph(nodes), start)
            if not lengths:
                break
            farthest = max(lengths, key=lengths.get)

            # 从最远节点BFS找真正的最远节点
            lengths2 = nx.single_source_shortest_path_length(mst.subgraph(nodes), farthest)
            if not lengths2:
                break
            farthest2 = max(lengths2, key=lengths2.get)

            # 获取最短路径
            path = nx.shortest_path(mst.subgraph(nodes), farthest, farthest2, weight='weight')

            # 从remaining中移除路径上的边
            for i in range(len(path)-1):
                edge = (path[i], path[i+1]) if (path[i], path[i+1]) in remaining_edges else (path[i+1], path[i])
                if edge in remaining_edges:
                    remaining_edges.remove(edge)

            lines.append({
                'name': f'Line {line_idx+1}',
                'stations': list(path),
            })

        except Exception as e:
            print(f"  分线错误: {e}")
            break

    # 如果还有剩余边, 添加到最近的线路
    if remaining_edges:
        for edge in remaining_edges:
            s1, s2 = edge
            # 找最近的线路
            best_line = 0
            best_d = 1e9
            for i, line in enumerate(lines):
                for sid in line['stations']:
                    d = to_m(sta_coords[s1], sta_coords[sid])
                    if d < best_d:
                        best_d, best_line = d, i
            # 添加到该线路
            if s1 not in lines[best_line]['stations']:
                lines[best_line]['stations'].append(s1)
            if s2 not in lines[best_line]['stations']:
                lines[best_line]['stations'].append(s2)

    # 如果没有线路, 创建一个包含所有站的线路
    if not lines:
        lines.append({
            'name': 'Line 1',
            'stations': list(remaining_nodes),
        })

    return lines

# ═══════════════════════════════════════════════════════════════
# 第6步: 转弯半径约束
# ═══════════════════════════════════════════════════════════════
def turn_radius(A, B, C):
    """计算A→B→C的转弯半径(米)"""
    BA = np.array([(A[0]-B[0])*M_LON, (A[1]-B[1])*M_LAT])
    BC = np.array([(C[0]-B[0])*M_LON, (C[1]-B[1])*M_LAT])
    len_BA = np.linalg.norm(BA)
    len_BC = np.linalg.norm(BC)
    if len_BA < 1 or len_BC < 1: return float('inf')
    cos_angle = np.clip(np.dot(BA, BC) / (len_BA * len_BC), -1, 1)
    angle_ABC = np.arccos(cos_angle)
    turn_angle = np.pi - angle_ABC
    if turn_angle < 0.01: return float('inf')
    d_avg = (len_BA + len_BC) / 2
    return d_avg / (2 * np.sin(turn_angle / 2))

def smooth_lines(lines):
    """删除转弯半径<1000m的违规中间点"""
    print("第6步: 转弯半径约束...")
    smoothed = []
    for line in lines:
        coords = line['coords']
        if len(coords) < 3:
            smoothed.append({**line, 'smooth_coords': coords})
            continue

        path = list(coords)
        for iteration in range(5):
            if len(path) < 3: break
            new_path = [path[0]]
            i = 1
            while i < len(path) - 1:
                A = new_path[-1]
                B = path[i]
                C = path[i+1]
                R = turn_radius(A, B, C)
                if R < R_MIN_M:
                    i += 1  # 删除B
                else:
                    new_path.append(B)
                    i += 1
            new_path.append(path[-1])
            path = new_path

        n_removed = len(coords) - len(path)
        print(f"  {line['name']}: {len(coords)}站 → {len(path)}站 (删除{n_removed}个违规点)")
        smoothed.append({**line, 'smooth_coords': path})

    return smoothed

# ═══════════════════════════════════════════════════════════════
# 第7步: 可视化
# ═══════════════════════════════════════════════════════════════
def visualize(gdf_pop, gdf_cand, gdf_sta, lines, G_road):
    print("第7步: 可视化...")
    west, south, east, north = BBOX
    fig, axes = plt.subplots(2, 2, figsize=(16, 14))

    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']

    # 左上: 人口 + 路网
    ax = axes[0,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#cccccc', edge_linewidth=0.3, show=False)
    gdf_pop.plot(ax=ax, column='pop', cmap='YlOrRd', markersize=3, alpha=0.7, legend=True)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('人口分布 + OSM路网', fontsize=13)

    # 右上: 候选 + 车站
    ax = axes[0,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.2, show=False)
    gdf_cand.plot(ax=ax, color='lightgreen', markersize=5, alpha=0.3)
    gdf_sta.plot(ax=ax, color='red', markersize=40, edgecolor='black', linewidth=0.5, zorder=5)
    for _, s in gdf_sta.iterrows():
        ax.annotate(s['station_id'], xy=(s.geometry.x, s.geometry.y), fontsize=6,
                    ha='center', va='bottom', xytext=(0,4), textcoords='offset points')
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title(f'100m聚类({len(gdf_cand)}候选) → 选站({len(gdf_sta)}站)', fontsize=13)

    # 左下: 线路拓扑
    ax = axes[1,0]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#eeeeee', edge_linewidth=0.15, show=False)

    # 画800m覆盖圈
    for _, s in gdf_sta.iterrows():
        circle = plt.Circle((s.geometry.x, s.geometry.y), WALK_R_M/111000, color='green', alpha=0.05)
        ax.add_patch(circle)

    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        sc = line['smooth_coords']
        ax.plot([p[0] for p in sc], [p[1] for p in sc], '-', color=c, linewidth=2.5, alpha=0.8, label=line['name'])
        ax.plot([p[0] for p in sc], [p[1] for p in sc], 'o', color=c, markersize=6)

    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=40, zorder=5)
    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title(f'线路拓扑 (最多{MAX_LINES}条, 连通)', fontsize=13)
    ax.legend(fontsize=8, loc='upper right')

    # 右下: 最终方案
    ax = axes[1,1]
    ox.plot_graph(G_road, ax=ax, node_size=0, edge_color='#dddddd', edge_linewidth=0.2, show=False)
    gdf_pop.plot(ax=ax, color='lightcoral', markersize=1, alpha=0.1)

    for k, line in enumerate(lines):
        c = colors[k % len(colors)]
        sc = line['smooth_coords']
        ax.plot([p[0] for p in sc], [p[1] for p in sc], '-', color=c, linewidth=3, alpha=0.85)

    gdf_sta.plot(ax=ax, color='white', edgecolor='black', markersize=50, zorder=5)
    for _, s in gdf_sta.iterrows():
        ax.annotate(s['station_id'], xy=(s.geometry.x, s.geometry.y), fontsize=7,
                    ha='center', va='bottom', xytext=(0,5), textcoords='offset points', fontweight='bold')

    ax.set_xlim(west,east); ax.set_ylim(south,north)
    ax.set_title('最终地铁方案 + OSM路网', fontsize=13)

    plt.tight_layout()
    plt.savefig(f'{OUTPUT}/metro_plan_v6.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  已保存: {OUTPUT}/metro_plan_v6.png")

# ═══════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════
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
    print("地铁规划 v6")
    print("聚类: 100m, 站距: 1000-1500m, 步行: 800m")
    print("线路: 最多5条, 必须连通")
    print("="*60)

    buildings, G_road = get_data()
    gdf_pop = make_population(buildings)
    gdf_cand = cluster_100m(gdf_pop)
    gdf_sta = greedy_select(gdf_cand)
    lines = build_connected_lines(gdf_sta)
    smoothed = smooth_lines(lines)
    export_geojson(gdf_sta, smoothed)
    visualize(gdf_pop, gdf_cand, gdf_sta, smoothed, G_road)

    print("\n✓ 完成!")

if __name__ == '__main__':
    main()
