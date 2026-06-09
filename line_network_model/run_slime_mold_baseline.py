#!/usr/bin/env python3
"""Physarum-style slime mold baseline for candidate rail-network design."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np

from line_network_model.line_model_config import OUTPUT_DIR
from line_network_model.run_baseline import edge_distance_m, route_turn_angles
from line_network_model.station_selection import load_roads, load_station_file, road_style


PNG_FILE = OUTPUT_DIR / "10_slime_mold_network.png"
EDGES_FILE = OUTPUT_DIR / "10_slime_mold_edges.csv"
SUMMARY_FILE = OUTPUT_DIR / "10_slime_mold_summary.csv"

K_NEAREST = 7
MAX_CANDIDATE_EDGE_M = 6_500.0
OD_PAIR_COUNT = 18
ITERATIONS = 180
FLOW_REINFORCEMENT = 0.42
CONDUCTANCE_DECAY = 0.08
INITIAL_CONDUCTANCE = 0.18
VALUE_EDGE_BONUS = 0.32
KEEP_EDGE_QUANTILE = 0.63
MAX_SELECTED_EDGES = 46
MIN_SELECTED_STATIONS = 36
MAX_NODE_DEGREE = 3
LEAF_PRUNE_ROUNDS = 2
RANDOM_SEED = 42


@dataclass(frozen=True)
class CandidateEdge:
    a: int
    b: int
    length_m: float
    conductance: float
    flow: float


@dataclass(frozen=True)
class ExtractionParams:
    keep_edge_quantile: float = KEEP_EDGE_QUANTILE
    max_selected_edges: int = MAX_SELECTED_EDGES
    min_selected_stations: int = MIN_SELECTED_STATIONS
    max_node_degree: int = MAX_NODE_DEGREE
    leaf_prune_rounds: int = LEAF_PRUNE_ROUNDS
    protected_top_stations: int = 22
    weak_leaf_conductance_quantile: float = 0.45


def station_value(station: dict) -> float:
    return float(station.get("total_value", station.get("score", 0.0)) or 0.0)


def pair_orientation(a: dict, b: dict) -> str:
    lat0 = math.radians((float(a["lat"]) + float(b["lat"])) / 2.0)
    dx = (float(b["lon"]) - float(a["lon"])) * 111_320.0 * math.cos(lat0)
    dy = (float(b["lat"]) - float(a["lat"])) * 110_540.0
    angle = abs(math.degrees(math.atan2(dy, dx)))
    if angle > 90.0:
        angle = 180.0 - angle
    if angle <= 30.0:
        return "horizontal"
    if angle >= 60.0:
        return "vertical"
    return "diagonal"


def build_candidate_edges(stations: list[dict]) -> list[CandidateEdge]:
    pairs = {}
    for i in range(len(stations)):
        distances = []
        for j in range(len(stations)):
            if i == j:
                continue
            d = edge_distance_m(i, j, stations)
            if d <= MAX_CANDIDATE_EDGE_M:
                distances.append((d, j))
        for d, j in sorted(distances)[:K_NEAREST]:
            a, b = sorted((i, j))
            value_scale = math.sqrt(station_value(stations[a]) * station_value(stations[b]))
            initial = INITIAL_CONDUCTANCE * (1.0 + VALUE_EDGE_BONUS * value_scale / 450_000.0)
            pairs[(a, b)] = max(pairs.get((a, b), 0.0), initial)
    return [
        CandidateEdge(a=a, b=b, length_m=edge_distance_m(a, b, stations), conductance=conductance, flow=0.0)
        for (a, b), conductance in sorted(pairs.items())
    ]


def od_pairs(stations: list[dict]) -> list[tuple[int, int, float]]:
    pairs = []
    for i in range(len(stations)):
        for j in range(i + 1, len(stations)):
            d = edge_distance_m(i, j, stations)
            if d < 4_000.0:
                continue
            reward = station_value(stations[i]) * station_value(stations[j]) / max(d, 1.0)
            pairs.append((i, j, reward))
    pairs.sort(key=lambda item: item[2], reverse=True)
    top = pairs[:OD_PAIR_COUNT]
    total = sum(weight for _, _, weight in top) or 1.0
    return [(a, b, weight / total) for a, b, weight in top]


def solve_pressures(
    n: int,
    edges: list[CandidateEdge],
    source: int,
    sink: int,
    demand: float,
) -> np.ndarray:
    laplacian = np.zeros((n, n), dtype=float)
    rhs = np.zeros(n, dtype=float)
    rhs[source] = demand
    rhs[sink] = -demand
    for edge in edges:
        conductance = max(edge.conductance, 1e-9)
        weight = conductance / max(edge.length_m, 1.0)
        a, b = edge.a, edge.b
        laplacian[a, a] += weight
        laplacian[b, b] += weight
        laplacian[a, b] -= weight
        laplacian[b, a] -= weight

    keep = [idx for idx in range(n) if idx != sink]
    reduced = laplacian[np.ix_(keep, keep)]
    reduced_rhs = rhs[keep]
    pressures = np.zeros(n, dtype=float)
    try:
        pressures[keep] = np.linalg.solve(reduced + np.eye(len(keep)) * 1e-9, reduced_rhs)
    except np.linalg.LinAlgError:
        pressures[keep] = np.linalg.lstsq(reduced + np.eye(len(keep)) * 1e-9, reduced_rhs, rcond=None)[0]
    pressures[sink] = 0.0
    return pressures


def iterate_slime_mold(stations: list[dict], edges: list[CandidateEdge]) -> list[CandidateEdge]:
    pairs = od_pairs(stations)
    current = list(edges)
    for _ in range(ITERATIONS):
        flow_accum = np.zeros(len(current), dtype=float)
        for source, sink, weight in pairs:
            pressures = solve_pressures(len(stations), current, source, sink, weight)
            for idx, edge in enumerate(current):
                flow = edge.conductance * abs(pressures[edge.a] - pressures[edge.b]) / max(edge.length_m, 1.0)
                flow_accum[idx] += flow

        max_flow = max(float(flow_accum.max()), 1e-12)
        next_edges = []
        for idx, edge in enumerate(current):
            normalized_flow = float(flow_accum[idx]) / max_flow
            conductance = (1.0 - CONDUCTANCE_DECAY) * edge.conductance + FLOW_REINFORCEMENT * normalized_flow
            next_edges.append(
                CandidateEdge(
                    a=edge.a,
                    b=edge.b,
                    length_m=edge.length_m,
                    conductance=conductance,
                    flow=float(flow_accum[idx]),
                )
            )
        current = next_edges
    return current


def selected_edge_set(
    edges: list[CandidateEdge],
    stations: list[dict],
    params: ExtractionParams | None = None,
) -> list[CandidateEdge]:
    params = params or ExtractionParams()
    threshold = float(np.quantile([edge.conductance for edge in edges], params.keep_edge_quantile))
    graph = nx.Graph()
    for edge in edges:
        graph.add_edge(edge.a, edge.b, weight=-edge.conductance, length_m=edge.length_m, edge=edge)
    high_value_nodes = set(
        sorted(range(len(stations)), key=lambda idx: station_value(stations[idx]), reverse=True)[: params.min_selected_stations]
    )
    strong_edges = [edge for edge in edges if edge.conductance >= threshold]
    selected = degree_limited_edge_selection(strong_edges, high_value_nodes, stations, params)
    selected = connect_selected_components(selected, graph, params)
    selected = prune_leaves(selected, stations, params)
    return selected[: params.max_selected_edges]


def degree_limited_edge_selection(
    candidate_edges: list[CandidateEdge],
    target_nodes: set[int],
    stations: list[dict],
    params: ExtractionParams,
) -> list[CandidateEdge]:
    selected = []
    degrees: dict[int, int] = {}
    ordered = sorted(
        candidate_edges,
        key=lambda edge: (
            edge.conductance / max(edge.length_m, 1.0),
            (station_value(stations[edge.a]) + station_value(stations[edge.b])) / max(edge.length_m, 1.0),
        ),
        reverse=True,
    )
    for edge in ordered:
        if len(selected) >= params.max_selected_edges:
            break
        if degrees.get(edge.a, 0) >= params.max_node_degree or degrees.get(edge.b, 0) >= params.max_node_degree:
            continue
        if edge.a not in target_nodes and edge.b not in target_nodes and edge.conductance < np.median([e.conductance for e in candidate_edges]):
            continue
        selected.append(edge)
        degrees[edge.a] = degrees.get(edge.a, 0) + 1
        degrees[edge.b] = degrees.get(edge.b, 0) + 1
    return selected


def connect_selected_components(
    selected: list[CandidateEdge],
    full_graph: nx.Graph,
    params: ExtractionParams,
) -> list[CandidateEdge]:
    by_pair = {(min(edge.a, edge.b), max(edge.a, edge.b)): edge for edge in selected}
    graph = nx.Graph()
    for edge in selected:
        graph.add_edge(edge.a, edge.b)
    if not graph.nodes:
        return selected
    max_repairs = max(1, params.max_selected_edges)
    for _ in range(max_repairs):
        if nx.number_connected_components(graph) <= 1:
            break
        components = [set(component) for component in nx.connected_components(graph)]
        best = None
        for left_idx, left in enumerate(components):
            for right in components[left_idx + 1 :]:
                for source in left:
                    for sink in right:
                        try:
                            path = nx.shortest_path(full_graph, source, sink, weight="length_m")
                        except nx.NetworkXNoPath:
                            continue
                        missing = []
                        length = 0.0
                        conductance = 0.0
                        for a, b in zip(path, path[1:]):
                            edge = full_graph[a][b]["edge"]
                            length += edge.length_m
                            conductance += edge.conductance
                            key = (min(a, b), max(a, b))
                            if key not in by_pair:
                                missing.append(edge)
                        score = length / max(conductance, 1e-9)
                        if best is None or score < best[0]:
                            best = (score, missing)
        if best is None:
            break
        added = False
        degrees = dict(graph.degree())
        for edge in best[1]:
            if (
                degrees.get(edge.a, 0) >= params.max_node_degree + 1
                or degrees.get(edge.b, 0) >= params.max_node_degree + 1
            ):
                continue
            key = (min(edge.a, edge.b), max(edge.a, edge.b))
            by_pair[key] = edge
            graph.add_edge(edge.a, edge.b)
            degrees[edge.a] = degrees.get(edge.a, 0) + 1
            degrees[edge.b] = degrees.get(edge.b, 0) + 1
            added = True
        if not added:
            break
    return list(by_pair.values())


def prune_leaves(edges: list[CandidateEdge], stations: list[dict], params: ExtractionParams | None = None) -> list[CandidateEdge]:
    params = params or ExtractionParams()
    current = list(edges)
    protected = set(
        sorted(range(len(stations)), key=lambda idx: station_value(stations[idx]), reverse=True)[
            : params.protected_top_stations
        ]
    )
    for _ in range(params.leaf_prune_rounds):
        degree = {}
        for edge in current:
            degree[edge.a] = degree.get(edge.a, 0) + 1
            degree[edge.b] = degree.get(edge.b, 0) + 1
        leaves = {node for node, deg in degree.items() if deg == 1 and node not in protected}
        if not leaves:
            break
        current = [edge for edge in current if edge.a not in leaves and edge.b not in leaves]
    current = prune_weak_leaves(current, stations, params, protected)
    return current


def prune_weak_leaves(
    edges: list[CandidateEdge],
    stations: list[dict],
    params: ExtractionParams,
    protected: set[int],
) -> list[CandidateEdge]:
    if not edges:
        return edges
    conductance_threshold = float(np.quantile([edge.conductance for edge in edges], params.weak_leaf_conductance_quantile))
    current = list(edges)
    while True:
        degree = {}
        incident = {}
        for edge in current:
            degree[edge.a] = degree.get(edge.a, 0) + 1
            degree[edge.b] = degree.get(edge.b, 0) + 1
            incident[edge.a] = edge
            incident[edge.b] = edge
        remove_nodes = set()
        for node, deg in degree.items():
            if deg != 1 or node in protected:
                continue
            edge = incident[node]
            if edge.conductance <= conductance_threshold:
                remove_nodes.add(node)
        if not remove_nodes:
            break
        next_edges = [edge for edge in current if edge.a not in remove_nodes and edge.b not in remove_nodes]
        if len({idx for edge in next_edges for idx in (edge.a, edge.b)}) < 18:
            break
        current = next_edges
    return current


def network_metrics(edges: list[CandidateEdge], stations: list[dict]) -> dict:
    nodes = {idx for edge in edges for idx in (edge.a, edge.b)}
    graph = nx.Graph()
    for edge in edges:
        graph.add_edge(edge.a, edge.b, length_m=edge.length_m)
    degrees = dict(graph.degree())
    edge_lengths = [edge.length_m for edge in edges]
    turn_angles = graph_turn_angles(graph, stations)
    orientation_counts = edge_orientation_counts(edges, stations)
    selected_edges = len(edges)
    return {
        "selected_stations": len(nodes),
        "selected_edges": selected_edges,
        "covered_value": sum(station_value(stations[idx]) for idx in nodes),
        "total_length_km": sum(edge_lengths) / 1000.0,
        "max_edge_km": max(edge_lengths, default=0.0) / 1000.0,
        "branch_nodes": sum(1 for degree in degrees.values() if degree > 2),
        "leaf_nodes": sum(1 for degree in degrees.values() if degree == 1),
        "components": nx.number_connected_components(graph) if graph.nodes else 0,
        "max_turn_deg": max(turn_angles, default=0.0),
        "mean_conductance": sum(edge.conductance for edge in edges) / max(len(edges), 1),
        "horizontal_edges": orientation_counts["horizontal"],
        "vertical_edges": orientation_counts["vertical"],
        "diagonal_edges": orientation_counts["diagonal"],
        "vertical_edge_share": orientation_counts["vertical"] / max(selected_edges, 1),
        "horizontal_edge_share": orientation_counts["horizontal"] / max(selected_edges, 1),
    }


def edge_orientation_counts(edges: list[CandidateEdge], stations: list[dict]) -> dict[str, int]:
    counts = {"horizontal": 0, "vertical": 0, "diagonal": 0}
    for edge in edges:
        counts[pair_orientation(stations[edge.a], stations[edge.b])] += 1
    return counts


def graph_turn_angles(graph: nx.Graph, stations: list[dict]) -> list[float]:
    angles = []
    for center in graph.nodes:
        neighbors = list(graph.neighbors(center))
        if len(neighbors) < 2:
            continue
        for pos, a in enumerate(neighbors):
            for b in neighbors[pos + 1 :]:
                angles.extend(route_turn_angles([a, center, b], stations))
    return angles


def map_bounds(stations: list[dict], roads: list[dict]) -> tuple[float, float, float, float]:
    lons = [station["lon"] for station in stations]
    lats = [station["lat"] for station in stations]
    for road in roads:
        for lon, lat in road["coords"]:
            lons.append(lon)
            lats.append(lat)
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)
    lon_pad = (east - west) * 0.04
    lat_pad = (north - south) * 0.04
    return west - lon_pad, south - lat_pad, east + lon_pad, north + lat_pad


def render_png(
    edges: list[CandidateEdge],
    stations: list[dict],
    roads: list[dict],
    path: Path,
    title_prefix: str = "Slime mold baseline",
) -> None:
    west, south, east, north = map_bounds(stations, roads)
    fig, ax = plt.subplots(figsize=(13, 9.2), dpi=160)
    ax.set_facecolor("#f8fafc")
    fig.patch.set_facecolor("#f8fafc")

    for road in roads:
        coords = [(lon, lat) for lon, lat in road["coords"] if west <= lon <= east and south <= lat <= north]
        if len(coords) < 2:
            continue
        color, width, opacity = road_style(str(road["highway"]))
        xs, ys = zip(*coords)
        ax.plot(xs, ys, color=color, linewidth=width * 0.50, alpha=opacity * 0.45, zorder=1)

    max_conductance = max((edge.conductance for edge in edges), default=1.0)
    for edge in sorted(edges, key=lambda item: item.conductance):
        a, b = edge.a, edge.b
        xs = [stations[a]["lon"], stations[b]["lon"]]
        ys = [stations[a]["lat"], stations[b]["lat"]]
        width = 1.2 + 5.5 * math.sqrt(edge.conductance / max(max_conductance, 1e-9))
        ax.plot(xs, ys, color="white", linewidth=width + 3.0, alpha=0.95, solid_capstyle="round", zorder=3)
        ax.plot(xs, ys, color="#7c3aed", linewidth=width, alpha=0.72, solid_capstyle="round", zorder=4)

    selected = {idx for edge in edges for idx in (edge.a, edge.b)}
    max_value = max(station_value(station) for station in stations)
    for idx, station in enumerate(stations):
        value = station_value(station)
        size = 18 + 88 * math.sqrt(value / max(max_value, 1.0))
        color = "#0f172a" if idx in selected else "#94a3b8"
        alpha = 0.92 if idx in selected else 0.30
        ax.scatter(station["lon"], station["lat"], s=size, color=color, edgecolor="white", linewidth=0.9, alpha=alpha, zorder=5)
        if idx in selected and value > 280_000:
            ax.text(
                station["lon"],
                station["lat"] + 0.0017,
                station["station_id"],
                ha="center",
                va="bottom",
                fontsize=7.4,
                color="#0f172a",
                weight="bold",
                zorder=6,
            )

    metrics = network_metrics(edges, stations)
    title = (
        f"{title_prefix}  "
        f"{metrics['selected_stations']} stations | {metrics['selected_edges']} edges | "
        f"{metrics['total_length_km']:.1f} km | branches {metrics['branch_nodes']}"
    )
    ax.set_title(title, loc="left", fontsize=13, weight="bold", color="#0f172a", pad=12)
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout(pad=0.8)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def write_edges_csv(edges: list[CandidateEdge], stations: list[dict]) -> None:
    with EDGES_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["from_station", "to_station", "length_km", "conductance", "flow"],
        )
        writer.writeheader()
        for edge in sorted(edges, key=lambda item: item.conductance, reverse=True):
            writer.writerow(
                {
                    "from_station": stations[edge.a]["station_id"],
                    "to_station": stations[edge.b]["station_id"],
                    "length_km": f"{edge.length_m / 1000.0:.3f}",
                    "conductance": f"{edge.conductance:.9f}",
                    "flow": f"{edge.flow:.9f}",
                }
            )


def write_summary_csv(
    edges: list[CandidateEdge],
    stations: list[dict],
    candidate_edge_count: int,
    params: ExtractionParams | None = None,
) -> None:
    params = params or ExtractionParams()
    metrics = network_metrics(edges, stations)
    metrics.update(
        {
            "candidate_edges": candidate_edge_count,
            "k_nearest": K_NEAREST,
            "od_pair_count": OD_PAIR_COUNT,
            "iterations": ITERATIONS,
            "conductance_decay": CONDUCTANCE_DECAY,
            "flow_reinforcement": FLOW_REINFORCEMENT,
            "keep_edge_quantile": params.keep_edge_quantile,
            "max_selected_edges": params.max_selected_edges,
            "min_selected_stations": params.min_selected_stations,
            "max_node_degree": params.max_node_degree,
            "leaf_prune_rounds": params.leaf_prune_rounds,
            "protected_top_stations": params.protected_top_stations,
            "weak_leaf_conductance_quantile": params.weak_leaf_conductance_quantile,
        }
    )
    with SUMMARY_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(metrics.keys()))
        writer.writeheader()
        writer.writerow(metrics)


def main() -> None:
    stations = load_station_file()
    roads = load_roads()
    candidate_edges = build_candidate_edges(stations)
    reinforced = iterate_slime_mold(stations, candidate_edges)
    selected = selected_edge_set(reinforced, stations)
    render_png(selected, stations, roads, PNG_FILE)
    write_edges_csv(selected, stations)
    write_summary_csv(selected, stations, len(candidate_edges))
    metrics = network_metrics(selected, stations)
    print("Slime mold baseline complete")
    print(f"  candidate_edges: {len(candidate_edges)}")
    print(f"  selected_stations: {metrics['selected_stations']}")
    print(f"  selected_edges: {metrics['selected_edges']}")
    print(f"  total_length_km: {metrics['total_length_km']:.2f}")
    print(f"  branch_nodes: {metrics['branch_nodes']}")
    print(f"  leaf_nodes: {metrics['leaf_nodes']}")
    print(f"  components: {metrics['components']}")
    print(f"  png: {PNG_FILE}")
    print(f"  edges_csv: {EDGES_FILE}")


if __name__ == "__main__":
    main()
