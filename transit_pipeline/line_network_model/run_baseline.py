#!/usr/bin/env python3
"""Classical baselines for the value-connection rail network model.

All baselines use the same objective:

    sum_{i<j selected} total_value_i * total_value_j / distance(i, j)

under a total construction-length budget. The implemented methods are simple
classical heuristics inspired by the literature notes: minimum-cost required
connection, greedy extension, greedy insertion, and prize/value-collecting tree.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
from pathlib import Path

from line_model_config import (
    MAX_TURN_ANGLE_DEG,
    TRANSPORT_CENTERS_REQUIRED,
    TURN_PENALTY_THRESHOLD_DEG,
    TURN_PENALTY_WEIGHT,
)
from station_selection import (
    STATION_FILE,
    distance_m,
    load_population,
    load_roads,
    load_station_file,
    road_style,
)


HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_TOTAL_LENGTH_KM = 80.0
MIN_EDGE_LENGTH_M = 1_000.0

PAIR_REWARD_FILE = OUTPUT_DIR / "02_pair_rewards.csv"
COMPARISON_FILE = OUTPUT_DIR / "05_baseline_comparison.csv"
COMPAT_GEOJSON_FILE = OUTPUT_DIR / "03_baseline_line.geojson"
COMPAT_SVG_FILE = OUTPUT_DIR / "04_baseline_line.svg"
EXISTING_METRO_LINES_FILE = OUTPUT_DIR / "00_existing_metro_lines.geojson"


def write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def station_value(station: dict) -> float:
    return float(station.get("total_value", station.get("score", 0.0)) or 0.0)


def load_existing_metro_lines() -> list[dict]:
    if not EXISTING_METRO_LINES_FILE.exists():
        return []
    data = read_json(EXISTING_METRO_LINES_FILE)
    return data.get("features", [])


def clip_segment_to_extent(
    p0: tuple[float, float],
    p1: tuple[float, float],
    west: float,
    south: float,
    east: float,
    north: float,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    x0, y0 = p0
    x1, y1 = p1
    dx = x1 - x0
    dy = y1 - y0
    t0, t1 = 0.0, 1.0

    for p, q in ((-dx, x0 - west), (dx, east - x0), (-dy, y0 - south), (dy, north - y0)):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return None
            t0 = max(t0, r)
        else:
            if r < t0:
                return None
            t1 = min(t1, r)

    return (x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy)


def clip_linestring_to_extent(
    coords: list[list[float]],
    west: float,
    south: float,
    east: float,
    north: float,
) -> list[list[tuple[float, float]]]:
    parts: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    lonlat = [(float(lon), float(lat)) for lon, lat in coords]

    for a, b in zip(lonlat, lonlat[1:]):
        clipped = clip_segment_to_extent(a, b, west, south, east, north)
        if clipped is None:
            if len(current) >= 2:
                parts.append(current)
            current = []
            continue
        c0, c1 = clipped
        if not current:
            current = [c0, c1]
        elif abs(current[-1][0] - c0[0]) < 1e-10 and abs(current[-1][1] - c0[1]) < 1e-10:
            current.append(c1)
        else:
            if len(current) >= 2:
                parts.append(current)
            current = [c0, c1]

    if len(current) >= 2:
        parts.append(current)
    return parts


def edge_distance_m(a: int, b: int, stations: list[dict]) -> float:
    return distance_m(
        (stations[a]["lon"], stations[a]["lat"]),
        (stations[b]["lon"], stations[b]["lat"]),
    )


def valid_edge(a: int, b: int, stations: list[dict]) -> bool:
    return edge_distance_m(a, b, stations) >= MIN_EDGE_LENGTH_M


def pair_reward(a: int, b: int, stations: list[dict]) -> float:
    d = max(edge_distance_m(a, b, stations), 1.0)
    return station_value(stations[a]) * station_value(stations[b]) / d


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
) -> float:
    ordered = sorted(selected)
    distances = shortest_path_distances(selected, edges, stations)
    total = 0.0
    for pos, a in enumerate(ordered):
        for b in ordered[pos + 1 :]:
            d = distances.get((a, b), float("inf"))
            if math.isfinite(d) and d > 0:
                total += station_value(stations[a]) * station_value(stations[b]) / d
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
    penalty = 0.0
    for angle in turn_angles:
        excess = max(0.0, angle - TURN_PENALTY_THRESHOLD_DEG)
        penalty += TURN_PENALTY_WEIGHT * (excess / 90.0) ** 2
    return penalty


def route_turn_angles(route: list[int], stations: list[dict]) -> list[float]:
    return [turn_angle_deg(a, b, c, stations) for a, b, c in zip(route, route[1:], route[2:])]


def route_turn_penalty(route: list[int], stations: list[dict]) -> float:
    return turn_penalty_from_angles(route_turn_angles(route, stations))


def route_turn_feasible(route: list[int], stations: list[dict]) -> bool:
    angles = route_turn_angles(route, stations)
    return not angles or max(angles) <= MAX_TURN_ANGLE_DEG


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


def network_turn_feasible(edges: list[tuple[int, int]], stations: list[dict]) -> bool:
    angles = network_turn_angles(edges, stations)
    return not angles or max(angles) <= MAX_TURN_ANGLE_DEG


def solution_turn_penalty(solution: dict, stations: list[dict]) -> float:
    if "route" in solution:
        return route_turn_penalty(solution["route"], stations)
    return network_turn_penalty(solution["edges"], stations)


def objective_score(solution: dict, stations: list[dict]) -> float:
    return total_pair_reward_for_network(solution["selected"], solution["edges"], stations) - solution_turn_penalty(solution, stations)


def objective_gain(candidate: dict, current: dict, stations: list[dict]) -> float:
    return objective_score(candidate, stations) - objective_score(current, stations)


def required_indices(stations: list[dict]) -> list[int]:
    if not TRANSPORT_CENTERS_REQUIRED:
        return []
    return [idx for idx, station in enumerate(stations) if station.get("required")]


def best_value_pair(stations: list[dict], budget_m: float) -> list[int]:
    best = None
    for i in range(len(stations)):
        for j in range(i + 1, len(stations)):
            d = edge_distance_m(i, j, stations)
            if d < MIN_EDGE_LENGTH_M or d > budget_m:
                continue
            reward = pair_reward(i, j, stations)
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


def initial_required_route(stations: list[dict], budget_m: float) -> list[int]:
    required = required_indices(stations)
    if not required:
        return best_value_pair(stations, budget_m)
    if len(required) <= 8:
        best_route = None
        best_key = None
        for candidate in itertools.permutations(required):
            route = list(candidate)
            if any(not valid_edge(a, b, stations) for a, b in route_edges(route)):
                continue
            length = route_length_m(route, stations)
            if length > budget_m:
                continue
            angles = route_turn_angles(route, stations)
            max_turn = max(angles) if angles else 0.0
            violation = max(0.0, max_turn - MAX_TURN_ANGLE_DEG)
            key = (violation, route_turn_penalty(route, stations), length)
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
            if length < best_length and route_turn_feasible(candidate, stations):
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
        route = best_value_pair(stations, MAX_TOTAL_LENGTH_KM * 1000.0)
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


def baseline_greedy_value_tree(stations: list[dict]) -> dict:
    budget_m = MAX_TOTAL_LENGTH_KM * 1000.0
    required = required_indices(stations)
    if required:
        route = initial_required_route(stations, budget_m)
        selected = set(route)
        edges = route_edges(route)
    else:
        route = best_value_pair(stations, budget_m)
        selected = set(route)
        edges = route_edges(route)

    current_length = network_length_m(edges, stations)
    unused = set(range(len(stations))) - selected
    while unused:
        current_solution = {"method": "greedy_value_tree", "selected": selected, "edges": edges}
        best = None
        for station_idx in unused:
            connector = nearest_selected_edge(station_idx, selected, stations)
            if connector is None:
                continue
            _, _, added_length = connector
            if current_length + added_length > budget_m:
                continue
            candidate_edges = edges + [(connector[0], connector[1])]
            if not network_turn_feasible(candidate_edges, stations):
                continue
            candidate_solution = {
                "method": "greedy_value_tree",
                "selected": selected | {station_idx},
                "edges": candidate_edges,
            }
            gain = objective_gain(candidate_solution, current_solution, stations)
            density = gain / max(added_length, 1.0)
            if best is None or density > best["density"]:
                best = {
                    "station_idx": station_idx,
                    "edge": (connector[0], connector[1]),
                    "added_length": added_length,
                    "gain": gain,
                    "density": density,
                }
        if best is None or best["gain"] <= 0:
            break
        selected.add(best["station_idx"])
        unused.remove(best["station_idx"])
        edges.append(best["edge"])
        current_length += best["added_length"]

    return {"method": "greedy_value_tree", "selected": selected, "edges": edges}


def solution_metrics(solution: dict, stations: list[dict]) -> dict:
    selected = solution["selected"]
    edges = solution["edges"]
    length_km = network_length_m(edges, stations) / 1000.0
    reward = total_pair_reward_for_network(selected, edges, stations)
    turn_angles = route_turn_angles(solution["route"], stations) if "route" in solution else network_turn_angles(edges, stations)
    turn_penalty = turn_penalty_from_angles(turn_angles)
    score = reward - turn_penalty
    required_selected = sum(1 for idx in selected if stations[idx].get("required")) if TRANSPORT_CENTERS_REQUIRED else 0
    return {
        "method": solution["method"],
        "selected_stations": len(selected),
        "selected_edges": len(edges),
        "selected_required": required_selected,
        "length_km": length_km,
        "pair_reward": reward,
        "turn_penalty": turn_penalty,
        "max_turn_deg": max(turn_angles) if turn_angles else 0.0,
        "penalized_turns": sum(1 for angle in turn_angles if angle > TURN_PENALTY_THRESHOLD_DEG),
        "objective_score": score,
        "objective_reward": score,
        "reward_per_km": score / max(length_km, 1e-9),
        "station_ids": ",".join(stations[i]["station_id"] for i in sorted(selected)),
    }


def output_paths(method: str) -> tuple[Path, Path]:
    return OUTPUT_DIR / f"03_baseline_{method}.geojson", OUTPUT_DIR / f"04_baseline_{method}.svg"


def write_network_geojson(stations: list[dict], solution: dict, path: Path) -> None:
    selected = solution["selected"]
    edges = solution["edges"]
    selected_stations = [stations[i] for i in sorted(selected)]
    features = []
    for edge_idx, (a, b) in enumerate(edges, start=1):
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [stations[a]["lon"], stations[a]["lat"]],
                        [stations[b]["lon"], stations[b]["lat"]],
                    ],
                },
                "properties": {
                    "edge_id": f"E{edge_idx:03d}",
                    "from_station": stations[a]["station_id"],
                    "to_station": stations[b]["station_id"],
                    "length_m": edge_distance_m(a, b, stations),
                },
            }
        )

    metrics = solution_metrics(solution, stations)
    features.insert(
        0,
        {
            "type": "Feature",
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [
                    [
                        [stations[a]["lon"], stations[a]["lat"]],
                        [stations[b]["lon"], stations[b]["lat"]],
                    ]
                    for a, b in edges
                ],
            },
            "properties": {
                "network_id": solution["method"],
                "method": solution["method"],
                "station_count": metrics["selected_stations"],
                "edge_count": metrics["selected_edges"],
                "length_km": metrics["length_km"],
                "pair_reward": metrics["pair_reward"],
                "turn_penalty": metrics["turn_penalty"],
                "max_turn_deg": metrics["max_turn_deg"],
                "penalized_turns": metrics["penalized_turns"],
                "objective_score": metrics["objective_score"],
                "objective_reward": metrics["objective_score"],
                "reward_per_km": metrics["reward_per_km"],
                "length_budget_km": MAX_TOTAL_LENGTH_KM,
                "station_ids": metrics["station_ids"],
            },
        },
    )

    for station in selected_stations:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [station["lon"], station["lat"]]},
                "properties": {
                    "station_id": station["station_id"],
                    "station_type": station.get("station_type", ""),
                    "required": station.get("required", False),
                    "population_value": station.get("population_value", 0.0),
                    "job_value": station.get("job_value", 0.0),
                    "transport_value": station.get("transport_value", 0.0),
                    "commercial_value": station.get("commercial_value", 0.0),
                    "total_value": station_value(station),
                    "layers": station.get("layers", ""),
                    "sources": station.get("sources", ""),
                    "component_count": station.get("component_count", 1),
                    "name": station.get("name", ""),
                },
            }
        )
    write_json(path, {"type": "FeatureCollection", "features": features})


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
    existing_metro_lines = load_existing_metro_lines()
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

    if existing_metro_lines:
        elements.append('<g id="existing-metro-reference">')
        for feature in existing_metro_lines:
            if feature.get("geometry", {}).get("type") != "LineString":
                continue
            coords = feature["geometry"].get("coordinates", [])
            color = feature.get("properties", {}).get("colour") or "#0f766e"
            for part in clip_linestring_to_extent(coords, west, south, east, north):
                points = [project(lon, lat) for lon, lat in part]
                if len(points) < 2:
                    continue
                point_attr = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
                elements.append(
                    f'<polyline points="{point_attr}" fill="none" stroke="{color}" '
                    'stroke-width="2.8" opacity="0.55" stroke-linecap="round" stroke-linejoin="round"/>'
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
        f'length {metrics["length_km"]:.1f}/{MAX_TOTAL_LENGTH_KM:.0f} km; score {metrics["objective_score"]:.1f}; '
        f'turn penalty {metrics["turn_penalty"]:.1f}; muted colored lines are existing metro reference'
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
        "turn_penalty",
        "max_turn_deg",
        "penalized_turns",
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
                    "turn_penalty": f'{row["turn_penalty"]:.6f}',
                    "max_turn_deg": f'{row["max_turn_deg"]:.3f}',
                    "penalized_turns": row["penalized_turns"],
                    "objective_score": f'{row["objective_score"]:.6f}',
                    "objective_reward": f'{row["objective_score"]:.6f}',
                    "reward_per_km": f'{row["reward_per_km"]:.6f}',
                    "station_ids": row["station_ids"],
                }
            )


def run_all_baselines(stations: list[dict]) -> list[dict]:
    return [
        baseline_required_mst(stations),
        baseline_greedy_value_tree(stations),
    ]


def main() -> None:
    if not STATION_FILE.exists():
        raise FileNotFoundError(
            f"{STATION_FILE} not found. Run: uv run python .\\transit_pipeline\\line_network_model\\01_select_stations.py"
        )

    population = load_population()
    roads = load_roads()
    stations = load_station_file()
    solutions = run_all_baselines(stations)
    metrics = [solution_metrics(solution, stations) for solution in solutions]

    write_pair_rewards(stations)
    write_comparison(metrics)
    for solution in solutions:
        geojson_path, svg_path = output_paths(solution["method"])
        write_network_geojson(stations, solution, geojson_path)
        write_svg(population, stations, solution, roads, svg_path)

    best = max(solutions, key=lambda solution: objective_score(solution, stations))
    best_geojson, best_svg = output_paths(best["method"])
    write_json(COMPAT_GEOJSON_FILE, json.loads(best_geojson.read_text(encoding="utf-8")))
    COMPAT_SVG_FILE.write_text(best_svg.read_text(encoding="utf-8"), encoding="utf-8")

    print("Classical baselines complete")
    print(f"  population points: {len(population)}")
    print(f"  road ways: {len(roads)}")
    print(f"  station candidates: {len(stations)}")
    for row in metrics:
        print(
            f"  {row['method']}: stations={row['selected_stations']}, "
            f"edges={row['selected_edges']}, length={row['length_km']:.2f} km, "
            f"score={row['objective_score']:.3f}, turn_penalty={row['turn_penalty']:.3f}, "
            f"max_turn={row['max_turn_deg']:.1f} deg"
        )
    print(f"  best_by_score: {best['method']}")
    print(f"  comparison: {COMPARISON_FILE}")


if __name__ == "__main__":
    main()
