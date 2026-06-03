#!/usr/bin/env python3
"""Classical baselines for the value-connection rail network model.

The greedy baseline is built in two stages. The first stage grows a connected
tree using the connectivity objective:

    sum_{i<j selected} total_value_i * total_value_j / distance(i, j)
    - lambda_1 * construction length
    - lambda_2 * turn-angle penalty

The second stage greedily prunes degree-1 leaves when doing so improves the
final objective. Endpoint count is reported as a diagnostic but is not part of
the score.
"""

from __future__ import annotations

import csv
import itertools
import math
from pathlib import Path

from line_network_model.line_model_config import TRANSPORT_CENTERS_REQUIRED
from line_network_model.objective_config import (
    CONSTRUCTION_COST_WEIGHT_PER_M,
    MIN_EDGE_LENGTH_M,
    PAIR_REWARD_CUTOFF_M,
    TURN_PENALTY_THRESHOLD_DEG,
    TURN_PENALTY_WEIGHT,
)
from line_network_model.station_selection import (
    STATION_TABLE_FILE,
    distance_m,
    load_population,
    load_roads,
    load_station_file,
    road_style,
)


HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PAIR_REWARD_FILE = OUTPUT_DIR / "02_pair_rewards.csv"
COMPARISON_FILE = OUTPUT_DIR / "05_baseline_comparison.csv"
COMPAT_SVG_FILE = OUTPUT_DIR / "04_baseline_line.svg"


def station_value(station: dict) -> float:
    return float(station.get("total_value", station.get("score", 0.0)) or 0.0)


def edge_distance_m(a: int, b: int, stations: list[dict]) -> float:
    return distance_m(
        (stations[a]["lon"], stations[a]["lat"]),
        (stations[b]["lon"], stations[b]["lat"]),
    )


def valid_edge(a: int, b: int, stations: list[dict]) -> bool:
    return edge_distance_m(a, b, stations) >= MIN_EDGE_LENGTH_M


def linear_pair_reward(
    value_a: float,
    value_b: float,
    distance_m: float,
    cutoff_m: float = PAIR_REWARD_CUTOFF_M,
) -> float:
    if cutoff_m <= 0:
        return 0.0
    decay = max(0.0, 1.0 - distance_m / cutoff_m)
    return value_a * value_b * decay


def pair_reward(
    a: int,
    b: int,
    stations: list[dict],
    cutoff_m: float = PAIR_REWARD_CUTOFF_M,
) -> float:
    return linear_pair_reward(
        station_value(stations[a]),
        station_value(stations[b]),
        edge_distance_m(a, b, stations),
        cutoff_m,
    )


def selected_from_edges(edges: list[tuple[int, int]]) -> set[int]:
    nodes = set()
    for a, b in edges:
        nodes.add(a)
        nodes.add(b)
    return nodes


def route_edges(route: list[int]) -> list[tuple[int, int]]:
    return list(zip(route, route[1:]))


def network_length_m(edges: list[tuple[int, int]], stations: list[dict]) -> float:
    return sum(edge_distance_m(a, b, stations) for a, b in edges)


def route_length_m(route: list[int], stations: list[dict]) -> float:
    return network_length_m(route_edges(route), stations)


def shortest_path_distances(
    selected: set[int],
    edges: list[tuple[int, int]],
    stations: list[dict],
) -> dict[tuple[int, int], float]:
    ordered = sorted(selected)
    dist: dict[tuple[int, int], float] = {}
    for i in ordered:
        dist[(i, i)] = 0.0
    for a, b in edges:
        if a not in selected or b not in selected:
            continue
        d = edge_distance_m(a, b, stations)
        dist[(a, b)] = min(dist.get((a, b), float("inf")), d)
        dist[(b, a)] = min(dist.get((b, a), float("inf")), d)

    for k in ordered:
        for i in ordered:
            dik = dist.get((i, k), float("inf"))
            if not math.isfinite(dik):
                continue
            for j in ordered:
                alt = dik + dist.get((k, j), float("inf"))
                if alt < dist.get((i, j), float("inf")):
                    dist[(i, j)] = alt
    return dist


def total_pair_reward_for_network(
    selected: set[int],
    edges: list[tuple[int, int]],
    stations: list[dict],
    cutoff_m: float = PAIR_REWARD_CUTOFF_M,
) -> float:
    ordered = sorted(selected)
    distances = shortest_path_distances(selected, edges, stations)
    total = 0.0
    for pos, a in enumerate(ordered):
        for b in ordered[pos + 1 :]:
            d = distances.get((a, b), float("inf"))
            if math.isfinite(d) and d > 0:
                total += linear_pair_reward(
                    station_value(stations[a]),
                    station_value(stations[b]),
                    d,
                    cutoff_m,
                )
    return total


def marginal_reward(station_idx: int, selected: set[int], stations: list[dict]) -> float:
    return sum(pair_reward(station_idx, selected_idx, stations) for selected_idx in selected)


def station_xy(station: dict) -> tuple[float, float]:
    lat0 = math.radians(station["lat"])
    x = station["lon"] * 111_320.0 * math.cos(lat0)
    y = station["lat"] * 110_540.0
    return x, y


def turn_angle_deg(prev_idx: int, center_idx: int, next_idx: int, stations: list[dict]) -> float:
    px, py = station_xy(stations[prev_idx])
    cx, cy = station_xy(stations[center_idx])
    nx, ny = station_xy(stations[next_idx])
    v1 = (px - cx, py - cy)
    v2 = (nx - cx, ny - cy)
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 <= 1e-9 or n2 <= 1e-9:
        return 0.0
    cos_theta = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    interior_angle = math.degrees(math.acos(cos_theta))
    return 180.0 - interior_angle


def turn_penalty_from_angles(turn_angles: list[float]) -> float:
    return TURN_PENALTY_WEIGHT * turn_excess_squared_from_angles(turn_angles)


def turn_excess_squared_from_angles(turn_angles: list[float]) -> float:
    total = 0.0
    for angle in turn_angles:
        excess = max(0.0, angle - TURN_PENALTY_THRESHOLD_DEG)
        total += (excess / 90.0) ** 2
    return total


def construction_cost(edges: list[tuple[int, int]], stations: list[dict]) -> float:
    return CONSTRUCTION_COST_WEIGHT_PER_M * network_length_m(edges, stations)


def route_turn_angles(route: list[int], stations: list[dict]) -> list[float]:
    return [turn_angle_deg(a, b, c, stations) for a, b, c in zip(route, route[1:], route[2:])]


def route_turn_penalty(route: list[int], stations: list[dict]) -> float:
    return turn_penalty_from_angles(route_turn_angles(route, stations))


def network_turn_angles(edges: list[tuple[int, int]], stations: list[dict]) -> list[float]:
    adjacency: dict[int, list[int]] = {}
    for a, b in edges:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    angles = []
    for center, neighbors in adjacency.items():
        for pos, a in enumerate(neighbors):
            for b in neighbors[pos + 1 :]:
                angles.append(turn_angle_deg(a, center, b, stations))
    return angles


def network_turn_penalty(edges: list[tuple[int, int]], stations: list[dict]) -> float:
    return turn_penalty_from_angles(network_turn_angles(edges, stations))


def node_degrees(edges: list[tuple[int, int]]) -> dict[int, int]:
    degrees: dict[int, int] = {}
    for a, b in edges:
        degrees[a] = degrees.get(a, 0) + 1
        degrees[b] = degrees.get(b, 0) + 1
    return degrees


def endpoint_count(selected: set[int], edges: list[tuple[int, int]]) -> int:
    degrees = node_degrees(edges)
    return sum(1 for idx in selected if degrees.get(idx, 0) == 1)


def endpoint_penalty(selected: set[int], edges: list[tuple[int, int]]) -> float:
    return 0.0


def score_from_components(
    components: dict,
    construction_weight_per_m: float = CONSTRUCTION_COST_WEIGHT_PER_M,
    turn_penalty_weight: float = TURN_PENALTY_WEIGHT,
) -> float:
    return (
        float(components["pair_reward"])
        - construction_weight_per_m * float(components["length_m"])
        - turn_penalty_weight * float(components["turn_excess_squared"])
    )


def solution_turn_penalty(solution: dict, stations: list[dict]) -> float:
    if "route" in solution:
        return route_turn_penalty(solution["route"], stations)
    return network_turn_penalty(solution["edges"], stations)


def solution_turn_angles(solution: dict, stations: list[dict]) -> list[float]:
    if "lines" in solution:
        angles = []
        for route in solution["lines"]:
            angles.extend(route_turn_angles(route, stations))
        return angles
    if "route" in solution:
        return route_turn_angles(solution["route"], stations)
    return network_turn_angles(solution["edges"], stations)


def solution_components(
    solution: dict,
    stations: list[dict],
    cutoff_m: float = PAIR_REWARD_CUTOFF_M,
    construction_weight_per_m: float = CONSTRUCTION_COST_WEIGHT_PER_M,
    turn_penalty_weight: float = TURN_PENALTY_WEIGHT,
) -> dict:
    selected = solution["selected"]
    edges = solution["edges"]
    turn_angles = solution_turn_angles(solution, stations)
    length_m = network_length_m(edges, stations)
    endpoints = endpoint_count(selected, edges)
    turn_excess_squared = turn_excess_squared_from_angles(turn_angles)
    pair_reward_value = total_pair_reward_for_network(selected, edges, stations, cutoff_m)
    construction_cost_value = construction_weight_per_m * length_m
    turn_penalty_value = turn_penalty_weight * turn_excess_squared
    endpoint_penalty_value = 0.0
    score = score_from_components(
        {
            "pair_reward": pair_reward_value,
            "length_m": length_m,
            "turn_excess_squared": turn_excess_squared,
            "endpoint_count": endpoints,
        },
        construction_weight_per_m=construction_weight_per_m,
        turn_penalty_weight=turn_penalty_weight,
    )
    return {
        "cutoff_m": cutoff_m,
        "pair_reward": pair_reward_value,
        "length_m": length_m,
        "turn_angles": turn_angles,
        "turn_excess_squared": turn_excess_squared,
        "endpoint_count": endpoints,
        "construction_cost": construction_cost_value,
        "turn_penalty": turn_penalty_value,
        "endpoint_penalty": endpoint_penalty_value,
        "connectivity_score": pair_reward_value - construction_cost_value - turn_penalty_value,
        "objective_score": score,
        "objective_reward": score,
    }


def connectivity_objective_score(solution: dict, stations: list[dict]) -> float:
    components = solution_components(solution, stations)
    return (
        components["pair_reward"]
        - components["construction_cost"]
        - components["turn_penalty"]
    )


def objective_score(solution: dict, stations: list[dict]) -> float:
    return solution_components(solution, stations)["objective_score"]


def objective_gain(candidate: dict, current: dict, stations: list[dict]) -> float:
    return objective_score(candidate, stations) - objective_score(current, stations)


def connectivity_objective_gain(candidate: dict, current: dict, stations: list[dict]) -> float:
    return connectivity_objective_score(candidate, stations) - connectivity_objective_score(current, stations)


def required_indices(stations: list[dict]) -> list[int]:
    if not TRANSPORT_CENTERS_REQUIRED:
        return []
    return [idx for idx, station in enumerate(stations) if station.get("required")]


def best_value_pair(stations: list[dict]) -> list[int]:
    best = None
    for i in range(len(stations)):
        for j in range(i + 1, len(stations)):
            d = edge_distance_m(i, j, stations)
            if d < MIN_EDGE_LENGTH_M:
                continue
            solution = {"method": "initial_pair", "selected": {i, j}, "edges": [(i, j)]}
            reward = connectivity_objective_score(solution, stations)
            if best is None or reward > best[0]:
                best = (reward, i, j)
    if best is None:
        return [max(range(len(stations)), key=lambda idx: station_value(stations[idx]))]
    return [best[1], best[2]]


def minimum_spanning_edges(nodes: list[int], stations: list[dict]) -> list[tuple[int, int]]:
    if len(nodes) <= 1:
        return []
    selected = {nodes[0]}
    remaining = set(nodes[1:])
    edges = []
    while remaining:
        best = None
        for a in selected:
            for b in remaining:
                d = edge_distance_m(a, b, stations)
                if best is None or d < best[0]:
                    best = (d, a, b)
        _, a, b = best
        edges.append((a, b))
        selected.add(b)
        remaining.remove(b)
    return edges


def initial_required_route(stations: list[dict]) -> list[int]:
    required = required_indices(stations)
    if not required:
        return best_value_pair(stations)
    if len(required) <= 8:
        best_route = None
        best_key = None
        for candidate in itertools.permutations(required):
            route = list(candidate)
            if any(not valid_edge(a, b, stations) for a, b in route_edges(route)):
                continue
            length = route_length_m(route, stations)
            angles = route_turn_angles(route, stations)
            solution = {"method": "required_route", "selected": set(route), "edges": route_edges(route), "route": route}
            key = (-connectivity_objective_score(solution, stations), route_turn_penalty(route, stations), length)
            if best_key is None or key < best_key:
                best_route = route
                best_key = key
        if best_route is not None:
            return best_route
    required.sort(key=lambda idx: station_value(stations[idx]), reverse=True)
    route = [required[0]]
    for station_idx in required[1:]:
        best_route = None
        best_length = float("inf")
        for pos in range(len(route) + 1):
            candidate = route[:pos] + [station_idx] + route[pos:]
            if any(not valid_edge(a, b, stations) for a, b in route_edges(candidate)):
                continue
            length = route_length_m(candidate, stations)
            if length < best_length:
                best_route = candidate
                best_length = length
        route = best_route if best_route is not None else route + [station_idx]
    return route


def write_pair_rewards(stations: list[dict]) -> None:
    with PAIR_REWARD_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "origin_id",
                "destination_id",
                "origin_value",
                "destination_value",
                "distance_m",
                "reward",
            ],
        )
        writer.writeheader()
        for i, origin in enumerate(stations):
            for j, destination in enumerate(stations[i + 1 :], start=i + 1):
                d = edge_distance_m(i, j, stations)
                writer.writerow(
                    {
                        "origin_id": origin["station_id"],
                        "destination_id": destination["station_id"],
                        "origin_value": f"{station_value(origin):.2f}",
                        "destination_value": f"{station_value(destination):.2f}",
                        "distance_m": f"{d:.2f}",
                        "reward": f"{pair_reward(i, j, stations):.6f}",
                    }
                )


def baseline_required_mst(stations: list[dict]) -> dict:
    required = required_indices(stations)
    if not required:
        route = best_value_pair(stations)
        edges = route_edges(route)
    else:
        edges = minimum_spanning_edges(required, stations)
    selected = selected_from_edges(edges) or set(required)
    return {"method": "required_mst", "selected": selected, "edges": edges}


def nearest_selected_edge(
    station_idx: int,
    selected: set[int],
    stations: list[dict],
) -> tuple[int, int, float] | None:
    best = None
    for anchor in selected:
        d = edge_distance_m(station_idx, anchor, stations)
        if d < MIN_EDGE_LENGTH_M:
            continue
        if best is None or d < best[2]:
            best = (station_idx, anchor, d)
    return best


def best_connectivity_edge(
    station_idx: int,
    selected: set[int],
    edges: list[tuple[int, int]],
    stations: list[dict],
    current_distances: dict[tuple[int, int], float] | None = None,
    current_turn_excess_squared: float | None = None,
) -> dict | None:
    if current_distances is None:
        current_distances = shortest_path_distances(selected, edges, stations)
    if current_turn_excess_squared is None:
        current_turn_excess_squared = turn_excess_squared_from_angles(network_turn_angles(edges, stations))

    best = None
    for anchor in selected:
        added_length = edge_distance_m(station_idx, anchor, stations)
        if added_length < MIN_EDGE_LENGTH_M:
            continue
        candidate_edges = edges + [(station_idx, anchor)]
        pair_gain = 0.0
        for selected_idx in selected:
            if selected_idx == anchor:
                path_distance = added_length
            else:
                anchor_distance = current_distances.get((anchor, selected_idx), float("inf"))
                path_distance = added_length + anchor_distance
            if math.isfinite(path_distance):
                pair_gain += linear_pair_reward(
                    station_value(stations[station_idx]),
                    station_value(stations[selected_idx]),
                    path_distance,
                )
        turn_excess_squared = turn_excess_squared_from_angles(network_turn_angles(candidate_edges, stations))
        turn_penalty = TURN_PENALTY_WEIGHT * turn_excess_squared
        turn_penalty_gain = TURN_PENALTY_WEIGHT * (turn_excess_squared - current_turn_excess_squared)
        gain = pair_gain - CONSTRUCTION_COST_WEIGHT_PER_M * added_length - turn_penalty_gain
        density = gain / max(added_length, 1.0)
        key = (density, gain, -turn_penalty, -added_length)
        if best is None or key > best["key"]:
            best = {
                "station_idx": station_idx,
                "edge": (station_idx, anchor),
                "added_length": added_length,
                "pair_gain": pair_gain,
                "gain": gain,
                "density": density,
                "turn_penalty": turn_penalty,
                "key": key,
            }
    return best


def prune_leaf_endpoints(solution: dict, stations: list[dict]) -> dict:
    required = set(required_indices(stations))
    selected = set(solution["selected"])
    edges = list(solution["edges"])

    while True:
        current_solution = {"method": solution["method"], "selected": selected, "edges": edges}
        current_score = objective_score(current_solution, stations)
        degrees = node_degrees(edges)
        leaves = [
            idx
            for idx in selected
            if degrees.get(idx, 0) == 1 and idx not in required and len(selected) > 2
        ]
        best = None
        for leaf in leaves:
            candidate_edges = [(a, b) for a, b in edges if a != leaf and b != leaf]
            candidate_selected = selected - {leaf}
            candidate_solution = {
                "method": solution["method"],
                "selected": candidate_selected,
                "edges": candidate_edges,
            }
            gain = objective_score(candidate_solution, stations) - current_score
            if best is None or gain > best["gain"]:
                best = {
                    "leaf": leaf,
                    "edges": candidate_edges,
                    "selected": candidate_selected,
                    "gain": gain,
                }
        if best is None or best["gain"] <= 0:
            break
        selected = best["selected"]
        edges = best["edges"]

    return {"method": solution["method"], "selected": selected, "edges": edges}


def baseline_greedy_value_tree(stations: list[dict]) -> dict:
    required = required_indices(stations)
    if required:
        route = initial_required_route(stations)
        selected = set(route)
        edges = route_edges(route)
    else:
        route = best_value_pair(stations)
        selected = set(route)
        edges = route_edges(route)

    unused = set(range(len(stations))) - selected
    while unused:
        best = None
        current_distances = shortest_path_distances(selected, edges, stations)
        current_turn_excess_squared = turn_excess_squared_from_angles(network_turn_angles(edges, stations))
        for station_idx in unused:
            candidate = best_connectivity_edge(
                station_idx,
                selected,
                edges,
                stations,
                current_distances=current_distances,
                current_turn_excess_squared=current_turn_excess_squared,
            )
            if candidate is None:
                continue
            if best is None or candidate["key"] > best["key"]:
                best = candidate
        if best is None or best["gain"] <= 0:
            break
        selected.add(best["station_idx"])
        unused.remove(best["station_idx"])
        edges.append(best["edge"])

    solution = {"method": "greedy_value_tree", "selected": selected, "edges": edges}
    return prune_leaf_endpoints(solution, stations)


def solution_metrics(solution: dict, stations: list[dict]) -> dict:
    selected = solution["selected"]
    edges = solution["edges"]
    components = solution_components(solution, stations)
    length_km = components["length_m"] / 1000.0
    turn_angles = components["turn_angles"]
    required_selected = sum(1 for idx in selected if stations[idx].get("required")) if TRANSPORT_CENTERS_REQUIRED else 0
    return {
        "method": solution["method"],
        "selected_stations": len(selected),
        "selected_edges": len(edges),
        "selected_required": required_selected,
        "length_km": length_km,
        "pair_reward": components["pair_reward"],
        "length_m": components["length_m"],
        "turn_excess_squared": components["turn_excess_squared"],
        "construction_cost": components["construction_cost"],
        "turn_penalty": components["turn_penalty"],
        "endpoint_count": components["endpoint_count"],
        "endpoint_penalty": components["endpoint_penalty"],
        "max_turn_deg": max(turn_angles) if turn_angles else 0.0,
        "penalized_turns": sum(1 for angle in turn_angles if angle > TURN_PENALTY_THRESHOLD_DEG),
        "connectivity_score": components["connectivity_score"],
        "objective_score": components["objective_score"],
        "objective_reward": components["objective_reward"],
        "reward_per_km": components["objective_score"] / max(length_km, 1e-9),
        "station_ids": ",".join(stations[i]["station_id"] for i in sorted(selected)),
    }


def output_path(method: str) -> Path:
    return OUTPUT_DIR / f"04_baseline_{method}.svg"


def value_color(value: float, max_value: float) -> str:
    ratio = min(max(value / max(max_value, 1e-9), 0.0), 1.0)
    stops = [
        (0.00, (148, 163, 184)),
        (0.35, (34, 197, 94)),
        (0.70, (37, 99, 235)),
        (1.00, (220, 38, 38)),
    ]
    for (lo_t, lo_rgb), (hi_t, hi_rgb) in zip(stops, stops[1:]):
        if ratio <= hi_t:
            blend = (ratio - lo_t) / max(hi_t - lo_t, 1e-9)
            rgb = tuple(round(lo + (hi - lo) * blend) for lo, hi in zip(lo_rgb, hi_rgb))
            return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    return "#dc2626"


def write_svg(
    population: list[dict],
    stations: list[dict],
    solution: dict,
    roads: list[dict],
    path: Path,
) -> None:
    selected = solution["selected"]
    edges = solution["edges"]
    width, height, pad = 1100, 850, 45
    lons = [s["lon"] for s in stations] + [p["lon"] for p in population]
    lats = [s["lat"] for s in stations] + [p["lat"] for p in population]
    for road in roads:
        for lon, lat in road["coords"]:
            lons.append(lon)
            lats.append(lat)
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)
    max_pop = max(point["pop"] for point in population)
    max_value = max(station_value(station) for station in stations)

    def project(lon: float, lat: float) -> tuple[float, float]:
        x = pad + (lon - west) / max(east - west, 1e-9) * (width - 2 * pad)
        y = height - pad - (lat - south) / max(north - south, 1e-9) * (height - 2 * pad)
        return x, y

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        f'<text x="45" y="34" font-family="Arial" font-size="22" font-weight="700" fill="#0f172a">Baseline: {solution["method"]}</text>',
    ]

    elements.append('<g id="road-basemap">')
    for road in roads:
        points = [project(lon, lat) for lon, lat in road["coords"]]
        if len(points) < 2:
            continue
        point_attr = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        color, stroke_width, opacity = road_style(str(road["highway"]))
        elements.append(
            f'<polyline points="{point_attr}" fill="none" stroke="{color}" '
            f'stroke-width="{stroke_width}" opacity="{opacity}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    elements.append("</g>")

    elements.append('<g id="population-points">')
    for point in population:
        x, y = project(point["lon"], point["lat"])
        r = 0.45 + 1.5 * math.sqrt(point["pop"] / max_pop)
        elements.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="#475569" opacity="0.13"/>')
    elements.append("</g>")

    elements.append('<g id="network-edges">')
    for a, b in edges:
        x1, y1 = project(stations[a]["lon"], stations[a]["lat"])
        x2, y2 = project(stations[b]["lon"], stations[b]["lat"])
        elements.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            'stroke="#2563eb" stroke-width="4.5" stroke-linecap="round" opacity="0.88"/>'
        )
    elements.append("</g>")

    elements.append('<g id="candidate-stations">')
    for idx, station in enumerate(stations):
        x, y = project(station["lon"], station["lat"])
        value = station_value(station)
        if idx in selected:
            fill = value_color(value, max_value)
            r = 5.5 + 7.5 * math.sqrt(value / max(max_value, 1e-9))
            opacity = 0.96
            stroke = "#ffffff"
        else:
            fill = "#94a3b8"
            r = 3
            opacity = 0.45
            stroke = "none"
        elements.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2" opacity="{opacity}"/>'
        )
    elements.append("</g>")

    for idx in sorted(selected):
        station = stations[idx]
        x, y = project(station["lon"], station["lat"])
        elements.append(
            f'<text x="{x + 8:.1f}" y="{y - 8:.1f}" font-family="Arial" '
            f'font-size="11" font-weight="700" fill="#0f172a">{station["station_id"]}</text>'
        )

    metrics = solution_metrics(solution, stations)
    elements.append(
        '<text x="45" y="815" font-family="Arial" font-size="13" fill="#334155">'
        f'length {metrics["length_km"]:.1f} km; score {metrics["objective_score"]:.1f}; '
        f'cost {metrics["construction_cost"]:.1f}; turns {metrics["turn_penalty"]:.1f}; '
        f'endpoints {metrics["endpoint_count"]}'
        '</text>'
    )
    elements.append("</svg>")
    path.write_text("\n".join(elements), encoding="utf-8")


def write_comparison(metrics: list[dict]) -> None:
    fieldnames = [
        "method",
        "selected_stations",
        "selected_edges",
        "selected_required",
        "length_km",
        "pair_reward",
        "construction_cost",
        "turn_penalty",
        "endpoint_count",
        "endpoint_penalty",
        "max_turn_deg",
        "penalized_turns",
        "connectivity_score",
        "objective_score",
        "objective_reward",
        "reward_per_km",
        "station_ids",
    ]
    with COMPARISON_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in metrics:
            writer.writerow(
                {
                    "method": row["method"],
                    "selected_stations": row["selected_stations"],
                    "selected_edges": row["selected_edges"],
                    "selected_required": row["selected_required"],
                    "length_km": f'{row["length_km"]:.3f}',
                    "pair_reward": f'{row["pair_reward"]:.6f}',
                    "construction_cost": f'{row["construction_cost"]:.6f}',
                    "turn_penalty": f'{row["turn_penalty"]:.6f}',
                    "endpoint_count": row["endpoint_count"],
                    "endpoint_penalty": f'{row["endpoint_penalty"]:.6f}',
                    "max_turn_deg": f'{row["max_turn_deg"]:.3f}',
                    "penalized_turns": row["penalized_turns"],
                    "connectivity_score": f'{row["connectivity_score"]:.6f}',
                    "objective_score": f'{row["objective_score"]:.6f}',
                    "objective_reward": f'{row["objective_score"]:.6f}',
                    "reward_per_km": f'{row["reward_per_km"]:.6f}',
                    "station_ids": row["station_ids"],
                }
            )


def run_all_baselines(stations: list[dict]) -> list[dict]:
    return [
        baseline_greedy_value_tree(stations),
    ]


def main() -> None:
    if not STATION_TABLE_FILE.exists():
        raise FileNotFoundError(
            f"{STATION_TABLE_FILE} not found. Run: uv run python .\\line_network_model\\01_select_stations.py"
        )

    population = load_population()
    roads = load_roads()
    stations = load_station_file()
    solutions = run_all_baselines(stations)
    metrics = [solution_metrics(solution, stations) for solution in solutions]

    write_pair_rewards(stations)
    write_comparison(metrics)
    for solution in solutions:
        svg_path = output_path(solution["method"])
        write_svg(population, stations, solution, roads, svg_path)

    best = max(solutions, key=lambda solution: objective_score(solution, stations))
    best_svg = output_path(best["method"])
    COMPAT_SVG_FILE.write_text(best_svg.read_text(encoding="utf-8"), encoding="utf-8")

    print("Classical baselines complete")
    print(f"  population points: {len(population)}")
    print(f"  road ways: {len(roads)}")
    print(f"  station candidates: {len(stations)}")
    for row in metrics:
        print(
            f"  {row['method']}: stations={row['selected_stations']}, "
            f"edges={row['selected_edges']}, length={row['length_km']:.2f} km, "
            f"score={row['objective_score']:.3f}, construction_cost={row['construction_cost']:.3f}, "
            f"turn_penalty={row['turn_penalty']:.3f}, "
            f"endpoints={row['endpoint_count']} (diagnostic), "
            f"max_turn={row['max_turn_deg']:.1f} deg"
        )
    print(f"  best_by_score: {best['method']}")
    print(f"  comparison: {COMPARISON_FILE}")


if __name__ == "__main__":
    main()
