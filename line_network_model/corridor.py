"""Candidate corridor graph generation."""

from __future__ import annotations

import itertools

import networkx as nx
import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors


def _station_ids(stations: pd.DataFrame) -> list[str]:
    return stations["station_id"].astype(str).tolist()


def _coords(stations: pd.DataFrame) -> np.ndarray:
    return stations[["x", "y"]].to_numpy(float)


def _empty_graph(stations: pd.DataFrame) -> nx.Graph:
    graph = nx.Graph()
    for row in stations.itertuples(index=False):
        graph.add_node(str(row.station_id), x=float(row.x), y=float(row.y), population_value=float(row.population_value))
    return graph


def _add_edge(graph: nx.Graph, u: str, v: str, distance: float, cost_per_km: float) -> None:
    if u == v:
        return
    cost = distance / 1_000.0 * cost_per_km
    if not graph.has_edge(u, v) or distance < graph[u][v]["distance"]:
        graph.add_edge(u, v, distance=float(distance), weight=float(distance), construction_cost=float(cost))


def _nearest_edges_by_node(ids: list[str], xy: np.ndarray) -> dict[str, list[tuple[float, str]]]:
    """Return every node's neighbors ordered by Euclidean distance."""
    ordered: dict[str, list[tuple[float, str]]] = {}
    for i, sid in enumerate(ids):
        neighbors = []
        for j, other in enumerate(ids):
            if i == j:
                continue
            neighbors.append((float(np.linalg.norm(xy[i] - xy[j])), other))
        ordered[sid] = sorted(neighbors)
    return ordered


def _reinforce_min_degree(
    graph: nx.Graph,
    ids: list[str],
    xy: np.ndarray,
    min_degree: int,
    cost_per_km: float,
) -> None:
    """Add local short edges until every station has at least min_degree candidate corridors."""
    if min_degree <= 0:
        return
    nearest = _nearest_edges_by_node(ids, xy)
    for sid in ids:
        for dist, other in nearest[sid]:
            if graph.degree[sid] >= min_degree:
                break
            _add_edge(graph, sid, other, dist, cost_per_km)


def _reinforce_edge_stations(
    graph: nx.Graph,
    ids: list[str],
    xy: np.ndarray,
    edge_quantile: float,
    edge_min_degree: int,
    cost_per_km: float,
) -> None:
    """Give spatial boundary stations extra local options so sparse outer areas are not starved."""
    if edge_min_degree <= 0 or edge_quantile <= 0:
        return
    x, y = xy[:, 0], xy[:, 1]
    low_x, high_x = np.quantile(x, [edge_quantile, 1.0 - edge_quantile])
    low_y, high_y = np.quantile(y, [edge_quantile, 1.0 - edge_quantile])
    boundary = {
        ids[i]
        for i, (px, py) in enumerate(xy)
        if px <= low_x or px >= high_x or py <= low_y or py >= high_y
    }
    nearest = _nearest_edges_by_node(ids, xy)
    for sid in boundary:
        for dist, other in nearest[sid]:
            if graph.degree[sid] >= edge_min_degree:
                break
            _add_edge(graph, sid, other, dist, cost_per_km)


def build_corridor_knn(stations: pd.DataFrame, k: int = 4, cost_per_km: float = 1.0) -> nx.Graph:
    """Build corridors by connecting every station to its k nearest neighbors."""
    graph = _empty_graph(stations)
    ids, xy = _station_ids(stations), _coords(stations)
    k_eff = min(k + 1, len(stations))
    nbrs = NearestNeighbors(n_neighbors=k_eff).fit(xy)
    distances, indices = nbrs.kneighbors(xy)
    for i, neighbors in enumerate(indices):
        for dist, j in zip(distances[i], neighbors):
            if i != j:
                _add_edge(graph, ids[i], ids[j], float(dist), cost_per_km)
    return graph


def build_corridor_radius(stations: pd.DataFrame, max_distance: float, cost_per_km: float = 1.0) -> nx.Graph:
    """Build corridors by connecting station pairs within max_distance."""
    graph = _empty_graph(stations)
    ids, xy = _station_ids(stations), _coords(stations)
    for i, j in itertools.combinations(range(len(ids)), 2):
        dist = float(np.linalg.norm(xy[i] - xy[j]))
        if dist <= max_distance:
            _add_edge(graph, ids[i], ids[j], dist, cost_per_km)
    return graph


def build_corridor_mst_plus(
    stations: pd.DataFrame,
    extra_edges_ratio: float = 0.3,
    cost_per_km: float = 1.0,
    min_degree: int = 3,
    edge_quantile: float = 0.2,
    edge_min_degree: int = 4,
) -> nx.Graph:
    """Build a connected MST backbone, then add short edges and local coverage safeguards.

    A pure MST-plus-shortest-edges strategy tends to spend most extra edges in the dense core.
    The min-degree and boundary reinforcements keep peripheral stations, such as southern
    stations in the Shanghai example, connected to several plausible local corridors.
    """
    base = _empty_graph(stations)
    ids, xy = _station_ids(stations), _coords(stations)
    complete = nx.Graph()
    for sid in ids:
        complete.add_node(sid)
    all_edges: list[tuple[float, str, str]] = []
    for i, j in itertools.combinations(range(len(ids)), 2):
        dist = float(np.linalg.norm(xy[i] - xy[j]))
        complete.add_edge(ids[i], ids[j], weight=dist)
        all_edges.append((dist, ids[i], ids[j]))

    mst = nx.minimum_spanning_tree(complete, weight="weight")
    for u, v, attrs in mst.edges(data=True):
        _add_edge(base, u, v, attrs["weight"], cost_per_km)

    extra_count = int(round(max(0.0, extra_edges_ratio) * max(1, len(ids) - 1)))
    added = 0
    for dist, u, v in sorted(all_edges):
        if not base.has_edge(u, v):
            _add_edge(base, u, v, dist, cost_per_km)
            added += 1
            if added >= extra_count:
                break
    _reinforce_min_degree(base, ids, xy, min_degree, cost_per_km)
    _reinforce_edge_stations(base, ids, xy, edge_quantile, edge_min_degree, cost_per_km)
    return base
