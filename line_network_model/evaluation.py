"""Network evaluation metrics for selected rail lines."""

from __future__ import annotations

import itertools
import random

import networkx as nx
import numpy as np
import pandas as pd

from line_network_model.line_pool import line_edges


def _station_index(stations: pd.DataFrame) -> dict[str, int]:
    return {str(sid): i for i, sid in enumerate(stations["station_id"].astype(str))}


def _euclidean(stations: pd.DataFrame) -> np.ndarray:
    xy = stations[["x", "y"]].to_numpy(float)
    diff = xy[:, None, :] - xy[None, :, :]
    return np.sqrt((diff * diff).sum(axis=2))


def build_selected_track_graph(selected_lines: list[dict], corridor_graph: nx.Graph) -> nx.Graph:
    """Build the physical track graph induced by selected line station sequences."""
    graph = nx.Graph()
    for line in selected_lines:
        seq = line["station_sequence"]
        for sid in seq:
            attrs = corridor_graph.nodes.get(sid, {})
            graph.add_node(sid, **attrs)
        for u, v in zip(seq[:-1], seq[1:]):
            attrs = dict(corridor_graph[u][v]) if corridor_graph.has_edge(u, v) else {"distance": 0.0, "construction_cost": 0.0}
            graph.add_edge(u, v, **attrs)
    return graph


def _line_transfer_graph(selected_lines: list[dict]) -> tuple[nx.Graph, dict[str, set[int]]]:
    graph = nx.Graph()
    station_to_lines: dict[str, set[int]] = {}
    for i, line in enumerate(selected_lines):
        graph.add_node(i)
        for sid in line["station_sequence"]:
            station_to_lines.setdefault(sid, set()).add(i)
    for lines in station_to_lines.values():
        for a, b in itertools.combinations(lines, 2):
            graph.add_edge(a, b)
    return graph, station_to_lines


def _direct_od_coverage(selected_lines: list[dict], od_matrix: np.ndarray, idx: dict[str, int]) -> float:
    covered_pairs = set()
    for line in selected_lines:
        for u, v in itertools.combinations(line["station_sequence"], 2):
            covered_pairs.add(tuple(sorted((u, v))))
    return float(sum(od_matrix[idx[u], idx[v]] + od_matrix[idx[v], idx[u]] for u, v in covered_pairs))


def _network_od_coverage(track_graph: nx.Graph, stations: pd.DataFrame, od_matrix: np.ndarray, idx: dict[str, int]) -> float:
    total = 0.0
    for comp in nx.connected_components(track_graph):
        comp = list(comp)
        for u, v in itertools.combinations(comp, 2):
            if u in idx and v in idx:
                total += od_matrix[idx[u], idx[v]] + od_matrix[idx[v], idx[u]]
    return float(total)


def _largest_component_ratio(graph: nx.Graph) -> float:
    if graph.number_of_nodes() == 0:
        return 0.0
    largest = max((len(c) for c in nx.connected_components(graph)), default=0)
    return largest / graph.number_of_nodes()


def _robustness(graph: nx.Graph, mode: str, remove_ratio: float = 0.1, trials: int = 20, seed: int = 42) -> float:
    if graph.number_of_nodes() <= 2:
        return _largest_component_ratio(graph)
    remove_n = max(1, int(round(graph.number_of_nodes() * remove_ratio)))
    nodes = list(graph.nodes)
    values = []
    if mode == "targeted":
        degree_rank = [n for n, _ in sorted(graph.degree, key=lambda x: x[1], reverse=True)]
        scenarios = [degree_rank[:remove_n]]
    else:
        rng = random.Random(seed)
        scenarios = [rng.sample(nodes, min(remove_n, len(nodes))) for _ in range(trials)]
    for removed in scenarios:
        g = graph.copy()
        g.remove_nodes_from(removed)
        values.append(_largest_component_ratio(g))
    return float(np.mean(values))


def evaluate_network(selected_lines: list[dict], stations: pd.DataFrame, od_matrix: np.ndarray, corridor_graph: nx.Graph) -> dict:
    """Evaluate cost, coverage, efficiency, transfer, structure, and simple robustness metrics."""
    idx = _station_index(stations)
    track_graph = build_selected_track_graph(selected_lines, corridor_graph)
    total_od = float(od_matrix.sum())
    pop = stations.set_index("station_id")["population_value"].astype(float)
    covered = set(track_graph.nodes)
    transfer_stations = {sid for sid in covered if sum(sid in line["station_sequence"] for line in selected_lines) >= 2}

    sum_line_length = float(sum(line["length"] for line in selected_lines))
    unique_track_edges = set().union(*(line_edges(line["station_sequence"]) for line in selected_lines)) if selected_lines else set()
    unique_track_length = 0.0
    unique_cost = 0.0
    for u, v in unique_track_edges:
        if corridor_graph.has_edge(u, v):
            unique_track_length += float(corridor_graph[u][v].get("distance", 0.0))
            unique_cost += float(corridor_graph[u][v].get("construction_cost", 0.0))

    direct_od = _direct_od_coverage(selected_lines, od_matrix, idx)
    network_od = _network_od_coverage(track_graph, stations, od_matrix, idx)

    euclid = _euclidean(stations)
    lengths = dict(nx.all_pairs_dijkstra_path_length(track_graph, weight="distance"))
    travel, weighted_travel, detour, weighted_detour = [], [], [], []
    reachable_weight = 0.0
    unreachable_weight = 0.0
    for i, j in itertools.combinations(range(len(stations)), 2):
        u = str(stations.iloc[i]["station_id"])
        v = str(stations.iloc[j]["station_id"])
        w = float(od_matrix[i, j] + od_matrix[j, i])
        if u in lengths and v in lengths[u]:
            d = float(lengths[u][v])
            travel.append(d)
            weighted_travel.append((d, w))
            if euclid[i, j] > 0:
                r = d / euclid[i, j]
                detour.append(r)
                weighted_detour.append((r, w))
            reachable_weight += w
        else:
            unreachable_weight += w

    transfer_graph, station_to_lines = _line_transfer_graph(selected_lines)
    transfers, weighted_transfers = [], []
    zero_w = one_w = two_plus_w = transfer_total_w = 0.0
    for i, j in itertools.combinations(range(len(stations)), 2):
        u = str(stations.iloc[i]["station_id"])
        v = str(stations.iloc[j]["station_id"])
        starts = station_to_lines.get(u, set())
        ends = station_to_lines.get(v, set())
        if not starts or not ends:
            continue
        best = None
        for a in starts:
            for b in ends:
                if a == b:
                    dist = 0
                else:
                    try:
                        dist = nx.shortest_path_length(transfer_graph, a, b)
                    except nx.NetworkXNoPath:
                        continue
                best = dist if best is None else min(best, dist)
        if best is None:
            continue
        w = float(od_matrix[i, j] + od_matrix[j, i])
        transfers.append(best)
        weighted_transfers.append((best, w))
        transfer_total_w += w
        if best == 0:
            zero_w += w
        elif best == 1:
            one_w += w
        else:
            two_plus_w += w

    n = track_graph.number_of_nodes()
    efficiency_sum = 0.0
    for u, v in itertools.permutations(track_graph.nodes, 2):
        d = lengths.get(u, {}).get(v)
        if d and d > 0:
            efficiency_sum += 1.0 / d

    return {
        "sum_line_length": sum_line_length,
        "unique_track_length": float(unique_track_length),
        "total_length": sum_line_length,
        "total_construction_cost": float(unique_cost),
        "num_lines": len(selected_lines),
        "num_stations_covered": len(covered),
        "num_transfer_stations": len(transfer_stations),
        "population_coverage": float(pop.reindex(list(covered)).fillna(0.0).sum()),
        "population_coverage_ratio": float(pop.reindex(list(covered)).fillna(0.0).sum() / max(pop.sum(), 1e-9)),
        "direct_od_coverage": direct_od,
        "direct_od_coverage_ratio": direct_od / max(total_od, 1e-9),
        "network_od_coverage": network_od,
        "network_od_coverage_ratio": network_od / max(total_od, 1e-9),
        "avg_travel_distance": float(np.mean(travel)) if travel else 0.0,
        "weighted_avg_travel_distance": float(np.average([x for x, _ in weighted_travel], weights=[w for _, w in weighted_travel])) if reachable_weight else 0.0,
        "avg_detour_ratio": float(np.mean(detour)) if detour else 0.0,
        "weighted_avg_detour_ratio": float(np.average([x for x, _ in weighted_detour], weights=[w for _, w in weighted_detour])) if reachable_weight else 0.0,
        "unreachable_od_ratio": unreachable_weight / max(total_od, 1e-9),
        "avg_transfer_count": float(np.mean(transfers)) if transfers else 0.0,
        "weighted_avg_transfer_count": float(np.average([x for x, _ in weighted_transfers], weights=[w for _, w in weighted_transfers])) if transfer_total_w else 0.0,
        "zero_transfer_od_ratio": zero_w / max(transfer_total_w, 1e-9),
        "one_transfer_od_ratio": one_w / max(transfer_total_w, 1e-9),
        "two_or_more_transfer_od_ratio": two_plus_w / max(transfer_total_w, 1e-9),
        "num_connected_components": nx.number_connected_components(track_graph) if n else 0,
        "largest_component_station_ratio": _largest_component_ratio(track_graph),
        "average_node_degree": float(np.mean([d for _, d in track_graph.degree])) if n else 0.0,
        "network_efficiency": efficiency_sum / max(n * (n - 1), 1),
        "robustness_random_station_removal": _robustness(track_graph, "random"),
        "robustness_targeted_station_removal": _robustness(track_graph, "targeted"),
    }

