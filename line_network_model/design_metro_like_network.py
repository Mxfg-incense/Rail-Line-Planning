#!/usr/bin/env python3
"""Generate metro-like line networks from fixed candidate stations.

This script treats the candidate stations and their values as the only fixed
input. It searches simple corridor paths, chooses a small set of open lines, and
renders a PNG for visual iteration.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt

from line_network_model.line_model_config import OUTPUT_DIR
from line_network_model.run_baseline import edge_distance_m, route_turn_angles
from line_network_model.station_selection import load_roads, load_station_file, road_style


PNG_FILE = OUTPUT_DIR / "09_metro_like_network.png"
LINES_FILE = OUTPUT_DIR / "09_metro_like_lines.csv"
SUMMARY_FILE = OUTPUT_DIR / "09_metro_like_summary.csv"

LINE_COUNT = 6
ORIENTATION_QUOTAS = {
    "vertical": 2,
    "horizontal": 2,
    "diagonal": 1,
    "free": 1,
}
MAX_ENDPOINTS = 46
CORRIDOR_BANDS_M = (1_000.0, 1_450.0, 1_900.0, 2_500.0, 3_100.0)
MIN_LINE_STATIONS = 5
MAX_LINE_STATIONS = 17
MAX_EDGE_LENGTH_M = 4_800.0
MIN_DIRECTNESS = 0.63
MAX_MEDIAN_TURN_DEG = 66.0
ENDPOINT_EXTENSION_MAX_M = 2_650.0
ENDPOINT_EXTENSION_ROUNDS = 2

LINE_COLORS = ["#e11d48", "#2563eb", "#16a34a", "#f59e0b", "#7c3aed", "#0891b2"]


@dataclass(frozen=True)
class CandidateLine:
    route: tuple[int, ...]
    orientation: str
    orientation_angle_deg: float
    score: float
    value: float
    length_m: float
    directness: float
    max_edge_m: float
    max_turn_deg: float
    median_turn_deg: float
    band_m: float


def station_value(station: dict) -> float:
    return float(station.get("total_value", station.get("score", 0.0)) or 0.0)


def xy_points(stations: list[dict]) -> list[tuple[float, float]]:
    lat0 = math.radians(sum(station["lat"] for station in stations) / len(stations))
    lon0 = sum(station["lon"] for station in stations) / len(stations)
    lat_ref = sum(station["lat"] for station in stations) / len(stations)
    return [
        (
            (station["lon"] - lon0) * 111_320.0 * math.cos(lat0),
            (station["lat"] - lat_ref) * 110_540.0,
        )
        for station in stations
    ]


def route_edges(route: tuple[int, ...]) -> list[tuple[int, int]]:
    return list(zip(route, route[1:]))


def route_length(route: tuple[int, ...], stations: list[dict]) -> float:
    return sum(edge_distance_m(a, b, stations) for a, b in route_edges(route))


def direct_distance(route: tuple[int, ...], stations: list[dict]) -> float:
    return edge_distance_m(route[0], route[-1], stations)


def route_orientation(route: tuple[int, ...], stations: list[dict]) -> tuple[str, float]:
    points = xy_points(stations)
    ax, ay = points[route[0]]
    bx, by = points[route[-1]]
    angle = abs(math.degrees(math.atan2(by - ay, bx - ax)))
    angle = min(angle, 180.0 - angle)
    if angle < 30.0:
        return "horizontal", angle
    if angle > 60.0:
        return "vertical", angle
    return "diagonal", angle


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def remove_duplicate_close_order(route: list[int], stations: list[dict]) -> list[int]:
    if len(route) <= 2:
        return route
    cleaned = [route[0]]
    for idx in route[1:]:
        if edge_distance_m(cleaned[-1], idx, stations) >= 850.0:
            cleaned.append(idx)
        elif station_value(stations[idx]) > station_value(stations[cleaned[-1]]):
            cleaned[-1] = idx
    if len(cleaned) >= 2 and cleaned[-1] != route[-1]:
        cleaned.append(route[-1])
    return cleaned


def smooth_route(route: tuple[int, ...], stations: list[dict]) -> tuple[int, ...]:
    current = list(route)
    changed = True
    while changed and len(current) > MIN_LINE_STATIONS:
        changed = False
        for pos in range(1, len(current) - 1):
            before = tuple(current)
            angle = route_turn_angles([current[pos - 1], current[pos], current[pos + 1]], stations)[0]
            if angle < 80.0:
                continue
            shortened = current[:pos] + current[pos + 1 :]
            if route_length(tuple(shortened), stations) <= route_length(before, stations) * 1.03:
                current = shortened
                changed = True
                break
    return tuple(current)


def corridor_route(
    a: int,
    b: int,
    band_m: float,
    stations: list[dict],
    points: list[tuple[float, float]],
) -> tuple[int, ...] | None:
    ax, ay = points[a]
    bx, by = points[b]
    vx, vy = bx - ax, by - ay
    length = math.hypot(vx, vy)
    if length < 6_000.0:
        return None

    included = []
    for idx, (x, y) in enumerate(points):
        wx, wy = x - ax, y - ay
        t = (wx * vx + wy * vy) / max(length * length, 1e-9)
        if t < -0.03 or t > 1.03:
            continue
        perp = abs(wx * vy - wy * vx) / length
        if idx not in {a, b} and perp > band_m:
            continue
        closeness = max(0.0, 1.0 - perp / max(band_m, 1.0))
        included.append((t, -station_value(stations[idx]), -closeness, idx))

    included.sort()
    route = [item[3] for item in included]
    route = remove_duplicate_close_order(route, stations)
    if route[0] != a:
        route = [a] + [idx for idx in route if idx != a]
    if route[-1] != b:
        route = [idx for idx in route if idx != b] + [b]
    if len(route) > MAX_LINE_STATIONS:
        required = {route[0], route[-1]}
        inner = route[1:-1]
        keep_count = MAX_LINE_STATIONS - 2
        ranked = sorted(inner, key=lambda idx: station_value(stations[idx]), reverse=True)[:keep_count]
        keep = required | set(ranked)
        route = [idx for idx in route if idx in keep]
    route_tuple = smooth_route(tuple(route), stations)
    return route_tuple if len(route_tuple) >= MIN_LINE_STATIONS else None


def route_signature(route: tuple[int, ...]) -> tuple[int, ...]:
    reverse = tuple(reversed(route))
    return min(route, reverse)


def line_candidate(
    route: tuple[int, ...],
    band_m: float,
    stations: list[dict],
) -> CandidateLine | None:
    length_m = route_length(route, stations)
    direct_m = direct_distance(route, stations)
    if length_m <= 0:
        return None
    directness = direct_m / length_m
    edge_lengths = [edge_distance_m(a, b, stations) for a, b in route_edges(route)]
    max_edge_m = max(edge_lengths, default=0.0)
    turns = route_turn_angles(list(route), stations)
    max_turn = max(turns, default=0.0)
    median_turn = median(turns)
    if directness < MIN_DIRECTNESS:
        return None
    if max_edge_m > MAX_EDGE_LENGTH_M:
        return None
    if max_turn > 115.0:
        return None
    if median_turn > MAX_MEDIAN_TURN_DEG:
        return None

    value = sum(station_value(stations[idx]) for idx in route)
    transfer_ready = sum(1 for idx in route[1:-1] if station_value(stations[idx]) > 250_000)
    turn_cost = sum(max(0.0, angle - 45.0) ** 2 for angle in turns)
    long_edge_cost = sum(max(0.0, edge - 2_900.0) ** 2 for edge in edge_lengths) / 1_000.0
    score = (
        value * (0.52 + 0.72 * directness)
        + transfer_ready * 110_000.0
        - length_m * 5.6
        - turn_cost * 1_800.0
        - long_edge_cost * 1_250.0
    )
    orientation, orientation_angle = route_orientation(route, stations)
    return CandidateLine(
        route=route,
        orientation=orientation,
        orientation_angle_deg=orientation_angle,
        score=score,
        value=value,
        length_m=length_m,
        directness=directness,
        max_edge_m=max_edge_m,
        max_turn_deg=max_turn,
        median_turn_deg=median_turn,
        band_m=band_m,
    )


def generate_candidates(stations: list[dict]) -> list[CandidateLine]:
    points = xy_points(stations)
    endpoint_pool = sorted(range(len(stations)), key=lambda idx: station_value(stations[idx]), reverse=True)[
        :MAX_ENDPOINTS
    ]
    candidates = {}
    for pos, a in enumerate(endpoint_pool):
        for b in endpoint_pool[pos + 1 :]:
            for band_m in CORRIDOR_BANDS_M:
                route = corridor_route(a, b, band_m, stations, points)
                if route is None:
                    continue
                candidate = line_candidate(route, band_m, stations)
                if candidate is None:
                    continue
                signature = route_signature(candidate.route)
                if signature not in candidates or candidate.score > candidates[signature].score:
                    candidates[signature] = candidate
    return sorted(candidates.values(), key=lambda item: item.score, reverse=True)


def shared_consecutive_edges(route: tuple[int, ...], selected_routes: list[tuple[int, ...]]) -> int:
    edges = {frozenset(edge) for edge in route_edges(route)}
    selected_edges = set()
    for selected in selected_routes:
        selected_edges.update(frozenset(edge) for edge in route_edges(selected))
    return len(edges & selected_edges)


def select_lines(candidates: list[CandidateLine], stations: list[dict]) -> list[CandidateLine]:
    selected: list[CandidateLine] = []
    covered: set[int] = set()
    selection_slots = []
    for orientation, count in ORIENTATION_QUOTAS.items():
        selection_slots.extend([orientation] * count)
    while len(selection_slots) < LINE_COUNT:
        selection_slots.append("free")

    for target_orientation in selection_slots[:LINE_COUNT]:
        best = None
        selected_routes = [line.route for line in selected]
        for candidate in candidates:
            if candidate in selected:
                continue
            if target_orientation != "free" and candidate.orientation != target_orientation:
                continue
            route_set = set(candidate.route)
            new_value = sum(station_value(stations[idx]) for idx in route_set - covered)
            overlap_value = sum(station_value(stations[idx]) for idx in route_set & covered)
            shared_edges = shared_consecutive_edges(candidate.route, selected_routes)
            if shared_edges > 1:
                continue
            transfer_count = len((route_set & covered) - {candidate.route[0], candidate.route[-1]})
            endpoint_reuse = sum(1 for endpoint in (candidate.route[0], candidate.route[-1]) if endpoint in covered)
            selection_score = (
                candidate.score
                + new_value * 1.65
                + transfer_count * 260_000.0
                - overlap_value * 0.92
                - endpoint_reuse * 180_000.0
                - shared_edges * 900_000.0
            )
            if target_orientation == "vertical":
                selection_score += vertical_grid_bonus(candidate, stations)
            elif target_orientation == "horizontal":
                selection_score += horizontal_grid_bonus(candidate, stations)
            if best is None or selection_score > best[0]:
                best = (selection_score, candidate)
        if best is None:
            break
        selected.append(best[1])
        covered.update(best[1].route)
    return extend_line_endpoints(selected, stations)


def vertical_grid_bonus(candidate: CandidateLine, stations: list[dict]) -> float:
    lats = [stations[idx]["lat"] for idx in candidate.route]
    lat_span = max(lats) - min(lats)
    lon_values = [stations[idx]["lon"] for idx in candidate.route]
    lon_mid = sum(station["lon"] for station in stations) / len(stations)
    centrality = 1.0 - min(abs(sum(lon_values) / len(lon_values) - lon_mid) / 0.075, 1.0)
    return lat_span * 28_000_000.0 + centrality * 420_000.0


def horizontal_grid_bonus(candidate: CandidateLine, stations: list[dict]) -> float:
    lons = [stations[idx]["lon"] for idx in candidate.route]
    lon_span = max(lons) - min(lons)
    lat_values = [stations[idx]["lat"] for idx in candidate.route]
    lat_mid = sum(station["lat"] for station in stations) / len(stations)
    centrality = 1.0 - min(abs(sum(lat_values) / len(lat_values) - lat_mid) / 0.055, 1.0)
    return lon_span * 18_000_000.0 + centrality * 260_000.0


def route_candidate_relaxed(route: tuple[int, ...], previous: CandidateLine, stations: list[dict]) -> CandidateLine | None:
    if len(route) > MAX_LINE_STATIONS + 2:
        return None
    length_m = route_length(route, stations)
    direct_m = direct_distance(route, stations)
    directness = direct_m / max(length_m, 1e-9)
    edge_lengths = [edge_distance_m(a, b, stations) for a, b in route_edges(route)]
    max_edge_m = max(edge_lengths, default=0.0)
    turns = route_turn_angles(list(route), stations)
    max_turn = max(turns, default=0.0)
    median_turn = median(turns)
    if max_edge_m > MAX_EDGE_LENGTH_M:
        return None
    if max_turn > 115.0:
        return None
    if median_turn > MAX_MEDIAN_TURN_DEG + 8.0:
        return None
    value = sum(station_value(stations[idx]) for idx in route)
    turn_cost = sum(max(0.0, angle - 50.0) ** 2 for angle in turns)
    long_edge_cost = sum(max(0.0, edge - 2_900.0) ** 2 for edge in edge_lengths) / 1_000.0
    score = value * (0.50 + 0.72 * directness) - length_m * 5.4 - turn_cost * 1_700.0 - long_edge_cost * 1_200.0
    orientation, orientation_angle = route_orientation(route, stations)
    return CandidateLine(
        route=route,
        orientation=orientation,
        orientation_angle_deg=orientation_angle,
        score=score,
        value=value,
        length_m=length_m,
        directness=directness,
        max_edge_m=max_edge_m,
        max_turn_deg=max_turn,
        median_turn_deg=median_turn,
        band_m=previous.band_m,
    )


def best_endpoint_extension(
    line: CandidateLine,
    covered: set[int],
    stations: list[dict],
) -> CandidateLine | None:
    best = None
    route = line.route
    for side, endpoint in (("start", route[0]), ("end", route[-1])):
        for idx in range(len(stations)):
            if idx in route:
                continue
            distance = edge_distance_m(endpoint, idx, stations)
            if distance > ENDPOINT_EXTENSION_MAX_M:
                continue
            is_new = idx not in covered
            if not is_new:
                continue
            west_s016_bonus = (
                210_000.0
                if stations[endpoint]["station_id"] == "S016" and stations[idx]["lon"] < stations[endpoint]["lon"]
                else 0.0
            )
            coverage_bonus = station_value(stations[idx]) * (1.45 if is_new else 0.25)
            extension_score = coverage_bonus + west_s016_bonus - distance * 26.0
            next_route = (idx,) + route if side == "start" else route + (idx,)
            candidate = route_candidate_relaxed(next_route, line, stations)
            if candidate is None:
                continue
            if best is None or extension_score > best[0]:
                best = (extension_score, candidate)
    if best is None or best[0] <= 0:
        return None
    return best[1]


def force_s016_west_extension(lines: list[CandidateLine], stations: list[dict]) -> list[CandidateLine]:
    by_id = {station["station_id"]: idx for idx, station in enumerate(stations)}
    s016 = by_id.get("S016")
    if s016 is None:
        return lines
    west_targets = [
        idx
        for idx, station in enumerate(stations)
        if station["lon"] < stations[s016]["lon"] and edge_distance_m(s016, idx, stations) <= ENDPOINT_EXTENSION_MAX_M
    ]
    west_targets.sort(key=lambda idx: (station_value(stations[idx]), -edge_distance_m(s016, idx, stations)), reverse=True)
    if not west_targets:
        return lines

    updated = []
    inserted = False
    for line in lines:
        if s016 not in line.route or inserted:
            updated.append(line)
            continue
        route = line.route
        target = west_targets[0]
        if route[0] == s016 and target not in route:
            candidate = route_candidate_relaxed((target,) + route, line, stations)
        elif route[-1] == s016 and target not in route:
            candidate = route_candidate_relaxed(route + (target,), line, stations)
        else:
            candidate = None
        updated.append(candidate or line)
        inserted = candidate is not None
    return updated


def extend_line_endpoints(lines: list[CandidateLine], stations: list[dict]) -> list[CandidateLine]:
    lines = force_s016_west_extension(lines, stations)
    for _ in range(ENDPOINT_EXTENSION_ROUNDS):
        covered = {idx for line in lines for idx in line.route}
        next_lines = []
        for line in lines:
            extension = best_endpoint_extension(line, covered, stations)
            if extension is None:
                next_lines.append(line)
            else:
                next_lines.append(extension)
                covered.update(extension.route)
        lines = next_lines
    return polish_high_turns(force_s016_presence(connect_isolated_lines(lines, stations), stations), stations)


def force_s016_presence(lines: list[CandidateLine], stations: list[dict]) -> list[CandidateLine]:
    by_id = {station["station_id"]: idx for idx, station in enumerate(stations)}
    s016 = by_id.get("S016")
    if s016 is None or any(s016 in line.route for line in lines):
        return lines
    replace_ids = {"S042", "S047", "S032"}
    best = None
    for line_pos, line in enumerate(lines):
        for pos, station_idx in enumerate(line.route):
            if pos == 0 or pos == len(line.route) - 1:
                continue
            if stations[station_idx]["station_id"] not in replace_ids:
                continue
            next_route = line.route[:pos] + (s016,) + line.route[pos + 1 :]
            if len(set(next_route)) != len(next_route):
                continue
            candidate = route_candidate_relaxed(next_route, line, stations)
            if candidate is None:
                continue
            has_west_neighbor = any(
                stations[idx]["lon"] < stations[s016]["lon"] and edge_distance_m(s016, idx, stations) <= 2_700.0
                for idx in (next_route[pos - 1], next_route[pos + 1])
            )
            key = (0 if has_west_neighbor else 1, candidate.length_m, -candidate.value)
            if best is None or key < best[0]:
                best = (key, line_pos, candidate)
    if best is None:
        return lines
    updated = list(lines)
    updated[best[1]] = best[2]
    return updated


def polish_high_turns(lines: list[CandidateLine], stations: list[dict]) -> list[CandidateLine]:
    return [polish_line_high_turns(line, stations) for line in lines]


def polish_line_high_turns(line: CandidateLine, stations: list[dict]) -> CandidateLine:
    protected_ids = {"S016"}
    protected = {idx for idx in line.route if stations[idx]["station_id"] in protected_ids}
    current = line
    changed = True
    while changed and len(current.route) > MIN_LINE_STATIONS:
        changed = False
        turns = route_turn_angles(list(current.route), stations)
        if not turns or max(turns) <= 100.0:
            break
        turn_pos = turns.index(max(turns)) + 1
        removal_positions = [turn_pos + 1, turn_pos - 1, turn_pos]
        best = None
        for remove_pos in removal_positions:
            if remove_pos <= 0 or remove_pos >= len(current.route) - 1:
                continue
            if current.route[remove_pos] in protected:
                continue
            next_route = current.route[:remove_pos] + current.route[remove_pos + 1 :]
            candidate = route_candidate_relaxed(next_route, current, stations)
            if candidate is None:
                continue
            next_max_turn = max(route_turn_angles(list(candidate.route), stations) or [0.0])
            key = (next_max_turn, candidate.length_m, -candidate.value)
            if best is None or key < best[0]:
                best = (key, candidate)
        if best is not None and best[1].length_m <= current.length_m * 1.02:
            current = best[1]
            changed = True
    return current


def connect_isolated_lines(lines: list[CandidateLine], stations: list[dict]) -> list[CandidateLine]:
    connected = list(lines)
    for line_pos, line in enumerate(connected):
        other_covered = {idx for pos, other in enumerate(connected) if pos != line_pos for idx in other.route}
        if set(line.route) & other_covered:
            continue
        best = None
        for side, endpoint in (("start", line.route[0]), ("end", line.route[-1])):
            for idx in other_covered:
                if idx in line.route:
                    continue
                distance = edge_distance_m(endpoint, idx, stations)
                if distance > 2_400.0:
                    continue
                next_route = (idx,) + line.route if side == "start" else line.route + (idx,)
                candidate = route_candidate_relaxed(next_route, line, stations)
                if candidate is None:
                    continue
                score = station_value(stations[idx]) - distance * 80.0
                if best is None or score > best[0]:
                    best = (score, candidate)
        if best is not None:
            connected[line_pos] = best[1]
    return connected


def network_metrics(lines: list[CandidateLine], stations: list[dict]) -> dict:
    covered = set()
    edge_count = 0
    station_line_counts: dict[int, int] = {}
    for line in lines:
        covered.update(line.route)
        for idx in set(line.route):
            station_line_counts[idx] = station_line_counts.get(idx, 0) + 1
        for a, b in route_edges(line.route):
            edge_count += 1
    transfer_nodes = sum(1 for count in station_line_counts.values() if count > 1)
    orientation_counts = {
        "horizontal_lines": sum(1 for line in lines if line.orientation == "horizontal"),
        "vertical_lines": sum(1 for line in lines if line.orientation == "vertical"),
        "diagonal_lines": sum(1 for line in lines if line.orientation == "diagonal"),
    }
    return {
        "line_count": len(lines),
        **orientation_counts,
        "selected_stations": len(covered),
        "selected_edges": edge_count,
        "covered_value": sum(station_value(stations[idx]) for idx in covered),
        "total_length_km": sum(line.length_m for line in lines) / 1000.0,
        "transfer_nodes": transfer_nodes,
        "branch_nodes": 0,
        "mean_directness": sum(line.directness for line in lines) / max(len(lines), 1),
        "max_edge_km": max((line.max_edge_m for line in lines), default=0.0) / 1000.0,
        "max_turn_deg": max((line.max_turn_deg for line in lines), default=0.0),
    }


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


def render_png(lines: list[CandidateLine], stations: list[dict], roads: list[dict], path: Path) -> None:
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
        ax.plot(xs, ys, color=color, linewidth=width * 0.55, alpha=opacity * 0.55, zorder=1)

    max_value = max(station_value(station) for station in stations)
    selected = {idx for line in lines for idx in line.route}
    for idx, station in enumerate(stations):
        value = station_value(station)
        size = 18 + 90 * math.sqrt(value / max(max_value, 1.0))
        color = "#94a3b8" if idx not in selected else "#0f172a"
        alpha = 0.34 if idx not in selected else 0.92
        ax.scatter(station["lon"], station["lat"], s=size, color=color, edgecolor="white", linewidth=0.8, alpha=alpha, zorder=3)

    for line_idx, line in enumerate(lines):
        color = LINE_COLORS[line_idx % len(LINE_COLORS)]
        xs = [stations[idx]["lon"] for idx in line.route]
        ys = [stations[idx]["lat"] for idx in line.route]
        ax.plot(xs, ys, color="white", linewidth=8.5, solid_capstyle="round", solid_joinstyle="round", zorder=4)
        ax.plot(xs, ys, color=color, linewidth=5.2, solid_capstyle="round", solid_joinstyle="round", zorder=5)
        for pos, idx in enumerate(line.route):
            station = stations[idx]
            ax.scatter(station["lon"], station["lat"], s=72, color="white", edgecolor=color, linewidth=2.0, zorder=6)
            if pos in {0, len(line.route) - 1} or station_value(station) > 280_000:
                ax.text(
                    station["lon"],
                    station["lat"] + 0.0018,
                    station["station_id"],
                    ha="center",
                    va="bottom",
                    fontsize=7.4,
                    color="#0f172a",
                    weight="bold",
                    zorder=7,
                )

    metrics = network_metrics(lines, stations)
    title = (
        "Metro-like candidate network  "
        f"{metrics['line_count']} lines | {metrics['selected_stations']} stations | "
        f"{metrics['total_length_km']:.1f} km | directness {metrics['mean_directness']:.2f}"
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


def write_lines_csv(lines: list[CandidateLine], stations: list[dict]) -> None:
    with LINES_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "line_id",
                "order",
                "station_id",
                "line_color",
                "orientation",
                "orientation_angle_deg",
                "line_length_km",
                "directness",
                "max_edge_km",
                "max_turn_deg",
                "median_turn_deg",
            ],
        )
        writer.writeheader()
        for line_idx, line in enumerate(lines, start=1):
            for order, station_idx in enumerate(line.route, start=1):
                writer.writerow(
                    {
                        "line_id": f"M{line_idx}",
                        "order": order,
                        "station_id": stations[station_idx]["station_id"],
                        "line_color": LINE_COLORS[(line_idx - 1) % len(LINE_COLORS)],
                        "orientation": line.orientation,
                        "orientation_angle_deg": f"{line.orientation_angle_deg:.3f}",
                        "line_length_km": f"{line.length_m / 1000.0:.3f}",
                        "directness": f"{line.directness:.4f}",
                        "max_edge_km": f"{line.max_edge_m / 1000.0:.3f}",
                        "max_turn_deg": f"{line.max_turn_deg:.3f}",
                        "median_turn_deg": f"{line.median_turn_deg:.3f}",
                    }
                )


def write_summary_csv(lines: list[CandidateLine], stations: list[dict], candidate_count: int) -> None:
    metrics = network_metrics(lines, stations)
    metrics["candidate_lines_generated"] = candidate_count
    with SUMMARY_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(metrics.keys()))
        writer.writeheader()
        writer.writerow(metrics)


def main() -> None:
    stations = load_station_file()
    roads = load_roads()
    candidates = generate_candidates(stations)
    if not candidates:
        raise RuntimeError("No metro-like line candidates generated.")
    lines = select_lines(candidates, stations)
    render_png(lines, stations, roads, PNG_FILE)
    write_lines_csv(lines, stations)
    write_summary_csv(lines, stations, len(candidates))

    metrics = network_metrics(lines, stations)
    print("Metro-like network design complete")
    print(f"  candidate_lines_generated: {len(candidates)}")
    print(f"  lines: {metrics['line_count']}")
    print(f"  selected_stations: {metrics['selected_stations']}")
    print(f"  total_length_km: {metrics['total_length_km']:.2f}")
    print(f"  mean_directness: {metrics['mean_directness']:.3f}")
    print(f"  max_edge_km: {metrics['max_edge_km']:.2f}")
    print(f"  transfer_nodes: {metrics['transfer_nodes']}")
    print(f"  branch_nodes: {metrics['branch_nodes']}")
    print(f"  png: {PNG_FILE}")
    print(f"  lines_csv: {LINES_FILE}")


if __name__ == "__main__":
    main()
