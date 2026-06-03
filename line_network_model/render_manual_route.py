#!/usr/bin/env python3
"""Render the manually specified intuition route as an SVG."""

from __future__ import annotations

from line_network_model.manual_route import (
    MANUAL_ROUTE_SVG_FILE,
    manual_solution,
    spacing_violations_for_lines,
)
from line_network_model.run_baseline import solution_metrics, station_value, value_color
from line_network_model.station_selection import STATION_TABLE_FILE, load_roads, load_station_file, road_style


def write_manual_svg(stations: list[dict], solution: dict, roads: list[dict]) -> None:
    selected = solution["selected"]
    lines = solution.get("lines", [])
    line_ids = solution.get("line_ids", [f"L{idx}" for idx in range(1, len(lines) + 1)])
    palette = ["#dc2626", "#2563eb", "#16a34a", "#9333ea", "#f97316", "#0891b2"]
    width, height, pad = 1100, 850, 45
    lons = [station["lon"] for station in stations]
    lats = [station["lat"] for station in stations]
    for road in roads:
        for lon, lat in road["coords"]:
            lons.append(lon)
            lats.append(lat)
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)
    max_value = max(station_value(station) for station in stations)

    def project(lon: float, lat: float) -> tuple[float, float]:
        x = pad + (lon - west) / max(east - west, 1e-9) * (width - 2 * pad)
        y = height - pad - (lat - south) / max(north - south, 1e-9) * (height - 2 * pad)
        return x, y

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        '<text x="45" y="34" font-family="Arial" font-size="22" font-weight="700" fill="#0f172a">Manual Intuition Route</text>',
        '<g id="road-basemap">',
    ]
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

    elements.append('<g id="candidate-stations">')
    for idx, station in enumerate(stations):
        x, y = project(station["lon"], station["lat"])
        value = station_value(station)
        if idx in selected:
            fill = value_color(value, max_value)
            r = 6.5 + 8.5 * (value / max(max_value, 1e-9)) ** 0.5
            stroke = "#ffffff"
            opacity = 0.96
        else:
            fill = "#94a3b8"
            r = 3.2
            stroke = "none"
            opacity = 0.46
        elements.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2" opacity="{opacity}"/>'
        )
    elements.append("</g>")

    elements.append('<g id="manual-route">')
    for line_idx, line in enumerate(lines):
        color = palette[line_idx % len(palette)]
        line_id = line_ids[line_idx]
        for edge_idx, (a, b) in enumerate(zip(line, line[1:]), start=1):
            x1, y1 = project(stations[a]["lon"], stations[a]["lat"])
            x2, y2 = project(stations[b]["lon"], stations[b]["lat"])
            elements.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{color}" stroke-width="5.5" stroke-linecap="round" opacity="0.90"/>'
            )
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            elements.append(
                f'<text x="{mx + 4:.1f}" y="{my - 4:.1f}" font-family="Arial" font-size="11" '
                f'font-weight="700" fill="{color}">{line_id}-{edge_idx}</text>'
            )
    elements.append("</g>")

    for line_idx, line in enumerate(lines):
        line_id = line_ids[line_idx]
        for pos, idx in enumerate(line, start=1):
            station = stations[idx]
            x, y = project(station["lon"], station["lat"])
            elements.append(
                f'<text x="{x + 8:.1f}" y="{y - 8:.1f}" font-family="Arial" font-size="12" '
                f'font-weight="700" fill="#0f172a">{line_id}.{pos} {station["station_id"]}</text>'
            )

    metrics = solution_metrics(solution, stations)
    elements.append(
        '<text x="45" y="815" font-family="Arial" font-size="13" fill="#334155">'
        f'length {metrics["length_km"]:.1f} km; score {metrics["objective_score"]:.1f}; '
        f'turn_excess {metrics["turn_excess_squared"]:.3f}; endpoints {metrics["endpoint_count"]}'
        '</text>'
    )
    elements.append("</svg>")
    MANUAL_ROUTE_SVG_FILE.write_text("\n".join(elements), encoding="utf-8")


def main() -> None:
    if not STATION_TABLE_FILE.exists():
        raise FileNotFoundError(
            f"{STATION_TABLE_FILE} not found. Run: uv run python -m line_network_model.01_select_stations"
        )

    roads = load_roads()
    stations = load_station_file()
    solution = manual_solution(stations)
    write_manual_svg(stations, solution, roads)

    metrics = solution_metrics(solution, stations)
    violations = spacing_violations_for_lines(solution["lines"], stations)
    print("Manual intuition route rendered")
    print(f"  selected stations: {metrics['selected_stations']}")
    print(f"  selected edges: {metrics['selected_edges']}")
    print(f"  length_km: {metrics['length_km']:.2f}")
    print(f"  pair_reward: {metrics['pair_reward']:.3f}")
    print(f"  turn_excess_squared: {metrics['turn_excess_squared']:.6f}")
    print(f"  endpoint_count: {metrics['endpoint_count']}")
    print(f"  objective_score: {metrics['objective_score']:.3f}")
    print(f"  spacing_violations: {len(violations)}")
    print(f"  svg: {MANUAL_ROUTE_SVG_FILE}")


if __name__ == "__main__":
    main()
