#!/usr/bin/env python3
"""诊断: 检查浦东区域内OSM建筑物和人口分布是否覆盖完整"""
import osmnx as ox
import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from shapely.geometry import box
import warnings
warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['Noto Sans CJK SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

BBOX = (121.48, 31.20, 121.56, 31.26)  # west, south, east, north
west, south, east, north = BBOX

print("=== 1. 获取建筑物数据 ===")
tags = {'building': True}
gdf_buildings = ox.features_from_bbox(bbox=BBOX, tags=tags)
print(f"建筑物总数: {len(gdf_buildings)}")
print(f"建筑物CRS: {gdf_buildings.crs}")

# 检查建筑物的经纬度范围
bounds = gdf_buildings.total_bounds  # [minx, miny, maxx, maxy]
print(f"建筑物范围: W={bounds[0]:.4f}, S={bounds[1]:.4f}, E={bounds[2]:.4f}, N={bounds[3]:.4f}")
print(f"请求区域:   W={west:.4f}, S={south:.4f}, E={east:.4f}, N={north:.4f}")

# 检查有多少建筑物在bbox内
bbox_poly = box(west, south, east, north)
gdf_in_bbox = gdf_buildings[gdf_buildings.geometry.intersects(bbox_poly)]
print(f"在bbox内的建筑物: {len(gdf_in_bbox)}")

print("\n=== 2. 获取道路网络 ===")
G = ox.graph_from_bbox(bbox=BBOX, network_type='drive')
print(f"节点: {len(G.nodes)}, 边: {len(G.edges)}")

print("\n=== 3. 绘制诊断图 ===")
fig, axes = plt.subplots(2, 2, figsize=(16, 14))

# 图1: 所有建筑物位置
ax1 = axes[0, 0]
gdf_buildings.plot(ax=ax1, color='red', markersize=0.5, alpha=0.5)
ax1.set_title(f'建筑物分布 (共{len(gdf_buildings)}个)', fontsize=14)
ax1.set_xlim(west, east)
ax1.set_ylim(south, north)

# 图2: 建筑物质心
ax2 = axes[0, 1]
centroids = gdf_buildings.geometry.centroid
centroids.plot(ax=ax2, color='blue', markersize=1, alpha=0.5)
ax2.set_title(f'建筑物质心分布', fontsize=14)
ax2.set_xlim(west, east)
ax2.set_ylim(south, north)

# 图3: 道路网络
ax3 = axes[1, 0]
ox.plot_graph(G, ax=ax3, node_size=0, edge_color='gray', edge_linewidth=0.3, show=False)
ax3.set_title(f'道路网络 ({len(G.nodes)}节点)', fontsize=14)
ax3.set_xlim(west, east)
ax3.set_ylim(south, north)

# 图4: 人口估计分布
ax4 = axes[1, 1]
residential_types = ['apartments', 'house', 'residential', 'dormitory', 'yes']
if 'building' in gdf_buildings.columns:
    mask = gdf_buildings['building'].isin(residential_types) | gdf_buildings['building'].isna()
    gdf_res = gdf_buildings[mask].copy()
else:
    gdf_res = gdf_buildings.copy()

gdf_res_proj = gdf_res.to_crs(epsg=3857)
gdf_res_proj['area'] = gdf_res_proj.geometry.area
gdf_res_proj = gdf_res_proj[gdf_res_proj['area'] > 10]
total_area = gdf_res_proj['area'].sum()
gdf_res_proj['pop'] = (gdf_res_proj['area'] / total_area * 500000).astype(int)

# 转回WGS84绘制
gdf_res_wgs = gdf_res_proj.to_crs(epsg=4326)
gdf_res_wgs['centroid'] = gdf_res_wgs.geometry.centroid
gdf_pop = gpd.GeoDataFrame({
    'geometry': gdf_res_wgs['centroid'].values,
    'pop': gdf_res_wgs['pop'].values
}, crs='EPSG:4326')

gdf_pop.plot(ax=ax4, column='pop', cmap='YlOrRd', markersize=2, alpha=0.5, legend=True)
ax4.set_title(f'人口分布 ({gdf_pop["pop"].sum()}人)', fontsize=14)
ax4.set_xlim(west, east)
ax4.set_ylim(south, north)

plt.tight_layout()
plt.savefig('/smb/j/SHT/COURSE-10/CS240/pj/transit_pipeline/output/diagnosis.png', dpi=150, bbox_inches='tight')
plt.close()
print("\n✓ 诊断图已保存: output/diagnosis.png")

# 统计每个网格的建筑物数量
print("\n=== 4. 网格化统计 ===")
nx_grid, ny_grid = 8, 6
x_bins = np.linspace(west, east, nx_grid + 1)
y_bins = np.linspace(south, north, ny_grid + 1)

centroids_arr = np.array([[p.x, p.y] for p in gdf_pop.geometry])
pops = gdf_pop['pop'].values

print(f"网格统计 ({nx_grid}x{ny_grid}):")
for i in range(ny_grid):
    row = ""
    for j in range(nx_grid):
        mask = ((centroids_arr[:, 0] >= x_bins[j]) & (centroids_arr[:, 0] < x_bins[j+1]) &
                (centroids_arr[:, 1] >= y_bins[i]) & (centroids_arr[:, 1] < y_bins[i+1]))
        count = mask.sum()
        pop_sum = pops[mask].sum()
        row += f" {pop_sum:>6d}"
    print(f"  y={y_bins[i]:.2f}-{y_bins[i+1]:.2f}: {row}")
