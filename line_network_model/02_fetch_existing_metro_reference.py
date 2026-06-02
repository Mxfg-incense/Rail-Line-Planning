#!/usr/bin/env python3
"""Fetch and plot existing metro/rail reference lines inside the study bbox."""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

from line_network_model.line_model_config import BBOX, CACHE_DIR, OUTPUT_DIR
from line_network_model.station_selection import load_roads, road_style


RAW_FILE = CACHE_DIR / f"existing_metro_{BBOX[0]}_{BBOX[1]}_{BBOX[2]}_{BBOX[3]}.json"
SVG_FILE = OUTPUT_DIR / "00_existing_metro_reference.svg"

DEFAULT_LINE_COLOR = "#0f766e"
RAILWAY_TYPES = {"subway", "light_rail", "rail", "monorail"}
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]


def inside_bbox(lon: float, lat: float, bbox: tuple[float, float, float, float] = BBOX) -> bool:
    west, south, east, north = bbox
    return west <= lon <= east and south <= lat <= north


def clip_segment_to_bbox(
    p0: tuple[float, float],
    p1: tuple[float, float],
    bbox: tuple[float, float, float, float] = BBOX,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Liang-Barsky line clipping in lon/lat coordinates."""
    west, south, east, north = bbox
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


def clip_linestring_to_bbox(
    coords: list[tuple[float, float]],
    bbox: tuple[float, float, float, float] = BBOX,
) -> list[list[tuple[float, float]]]:
    segments: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []

    for a, b in zip(coords, coords[1:]):
        clipped = clip_segment_to_bbox(a, b, bbox)
        if clipped is None:
            if len(current) >= 2:
                segments.append(current)
            current = []
            continue
        c0, c1 = clipped
        if not current:
            current = [c0, c1]
        elif abs(current[-1][0] - c0[0]) < 1e-10 and abs(current[-1][1] - c0[1]) < 1e-10:
            current.append(c1)
        else:
            if len(current) >= 2:
                segments.append(current)
            current = [c0, c1]

    if len(current) >= 2:
        segments.append(current)
    return segments


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def overpass_payload() -> str:
    west, south, east, north = BBOX
    bbox = f"{south},{west},{north},{east}"
    return f"""
    [out:json][timeout:180];
    (
      way["railway"~"^(subway|light_rail|monorail)$"]({bbox});
      relation["route"~"^(subway|light_rail|monorail)$"]({bbox});
      node["railway"="station"]["station"="subway"]({bbox});
      node["railway"="station"]["subway"="yes"]({bbox});
      node["public_transport"="station"]["subway"="yes"]({bbox});
    );
    out body;
    >;
    out skel qt;
    """


def light_overpass_payload() -> str:
    west, south, east, north = BBOX
    bbox = f"{south},{west},{north},{east}"
    return f"""
    [out:json][timeout:120];
    (
      way["railway"~"^(subway|light_rail|monorail)$"]({bbox});
      node["railway"="station"]["station"="subway"]({bbox});
      node["railway"="station"]["subway"="yes"]({bbox});
      node["public_transport"="station"]["subway"="yes"]({bbox});
    );
    out body;
    >;
    out skel qt;
    """


def fetch_existing_metro() -> dict:
    if RAW_FILE.exists():
        return read_json(RAW_FILE)

    errors = []
    for payload_fn in (overpass_payload, light_overpass_payload):
        data = urllib.parse.urlencode({"data": payload_fn()}).encode("utf-8")
        for endpoint in OVERPASS_ENDPOINTS:
            request = urllib.request.Request(
                endpoint,
                data=data,
                headers={"User-Agent": "CS240-line-network-model/1.0"},
            )
            try:
                with urllib.request.urlopen(request, timeout=240) as response:
                    payload = response.read().decode("utf-8")
                RAW_FILE.write_text(payload, encoding="utf-8")
                return json.loads(payload)
            except Exception as exc:
                errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("Could not fetch existing metro reference from Overpass:\n" + "\n".join(errors))


def normalize_color(color: str | None) -> str:
    if not color:
        return DEFAULT_LINE_COLOR
    color = color.strip()
    if color.startswith("#") and len(color) in {4, 7}:
        return color
    named = {
        "red": "#dc2626",
        "green": "#16a34a",
        "blue": "#2563eb",
        "yellow": "#eab308",
        "purple": "#9333ea",
        "orange": "#f97316",
        "pink": "#db2777",
        "brown": "#92400e",
        "grey": "#64748b",
        "gray": "#64748b",
    }
    return named.get(color.lower(), DEFAULT_LINE_COLOR)


def relation_label(tags: dict) -> str:
    return tags.get("ref") or tags.get("name:zh-Hans") or tags.get("name") or tags.get("name:en") or ""


def parse_reference(raw: dict) -> tuple[list[dict], list[dict]]:
    nodes = {
        el["id"]: (float(el["lon"]), float(el["lat"]))
        for el in raw.get("elements", [])
        if el.get("type") == "node" and "lon" in el and "lat" in el
    }
    way_relations: dict[int, list[dict]] = {}
    for element in raw.get("elements", []):
        if element.get("type") != "relation":
            continue
        tags = element.get("tags", {})
        if tags.get("route") not in {"subway", "light_rail", "monorail"}:
            continue
        rel_info = {
            "route_id": element.get("id"),
            "line_name": relation_label(tags),
            "route": tags.get("route", ""),
            "colour": normalize_color(tags.get("colour") or tags.get("color")),
        }
        for member in element.get("members", []):
            if member.get("type") == "way":
                way_relations.setdefault(member.get("ref"), []).append(rel_info)

    line_features = []
    seen_lines = set()
    for element in raw.get("elements", []):
        if element.get("type") != "way":
            continue
        tags = element.get("tags", {})
        railway = tags.get("railway", "")
        if railway not in RAILWAY_TYPES:
            continue
        coords = [nodes[node_id] for node_id in element.get("nodes", []) if node_id in nodes]
        if len(coords) < 2:
            continue
        rels = way_relations.get(element.get("id"), [])
        label = " / ".join(sorted({rel["line_name"] for rel in rels if rel.get("line_name")})) or tags.get("name", "")
        color = rels[0]["colour"] if rels else normalize_color(tags.get("colour") or tags.get("color"))
        for part_idx, clipped_coords in enumerate(clip_linestring_to_bbox(coords)):
            key = (
                element.get("id"),
                part_idx,
                tuple((round(lon, 7), round(lat, 7)) for lon, lat in clipped_coords),
            )
            if key in seen_lines:
                continue
            seen_lines.add(key)
            line_features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lon, lat in clipped_coords]},
                    "properties": {
                        "osm_id": element.get("id"),
                        "part": part_idx,
                        "railway": railway,
                        "line_name": label,
                        "colour": color,
                        "route_ids": ",".join(str(rel["route_id"]) for rel in rels),
                    },
                }
            )

    station_features = []
    seen_stations = set()
    for element in raw.get("elements", []):
        if element.get("type") != "node" or "lon" not in element or "lat" not in element:
            continue
        if not inside_bbox(float(element["lon"]), float(element["lat"])):
            continue
        tags = element.get("tags", {})
        is_station = (
            tags.get("railway") == "station"
            and (tags.get("station") == "subway" or tags.get("subway") == "yes")
        ) or (tags.get("public_transport") == "station" and tags.get("subway") == "yes")
        if not is_station:
            continue
        name = tags.get("name:zh-Hans") or tags.get("name") or tags.get("name:en") or ""
        key = (round(float(element["lon"]), 7), round(float(element["lat"]), 7), name)
        if key in seen_stations:
            continue
        seen_stations.add(key)
        station_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(element["lon"]), float(element["lat"])]},
                "properties": {"osm_id": element.get("id"), "name": name},
            }
        )
    return line_features, station_features


def write_svg(line_features: list[dict], station_features: list[dict]) -> None:
    roads = load_roads()
    width, height, pad = 1100, 850, 45
    west, south, east, north = BBOX

    def project(lon: float, lat: float) -> tuple[float, float]:
        x = pad + (lon - west) / max(east - west, 1e-9) * (width - 2 * pad)
        y = height - pad - (lat - south) / max(north - south, 1e-9) * (height - 2 * pad)
        return x, y

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        '<text x="45" y="34" font-family="Arial" font-size="22" font-weight="700" fill="#0f172a">Existing Metro Reference in Current BBOX</text>',
        '<g id="road-basemap">',
    ]
    for road in roads:
        points = [project(lon, lat) for lon, lat in road["coords"]]
        if len(points) < 2:
            continue
        point_attr = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        color, stroke_width, opacity = road_style(str(road["highway"]))
        elements.append(
            f'<polyline points="{point_attr}" fill="none" stroke="{color}" stroke-width="{stroke_width}" '
            f'opacity="{opacity * 0.65:.2f}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    elements.append("</g>")

    elements.append('<g id="metro-lines">')
    for feature in line_features:
        coords = [project(lon, lat) for lon, lat in feature["geometry"]["coordinates"]]
        point_attr = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        color = feature["properties"].get("colour") or DEFAULT_LINE_COLOR
        elements.append(
            f'<polyline points="{point_attr}" fill="none" stroke="{color}" stroke-width="3.2" '
            'opacity="0.88" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    elements.append("</g>")

    elements.append('<g id="metro-stations">')
    for feature in station_features:
        lon, lat = feature["geometry"]["coordinates"]
        x, y = project(lon, lat)
        elements.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.4" fill="#ffffff" stroke="#0f172a" stroke-width="1.2"/>')
    elements.append("</g>")

    labelled = 0
    for feature in station_features:
        name = feature["properties"].get("name", "")
        if not name or labelled >= 45:
            continue
        lon, lat = feature["geometry"]["coordinates"]
        x, y = project(lon, lat)
        elements.append(
            f'<text x="{x + 5:.1f}" y="{y - 5:.1f}" font-family="Arial" font-size="9" fill="#0f172a">{name}</text>'
        )
        labelled += 1

    elements.append(
        '<text x="45" y="815" font-family="Arial" font-size="13" fill="#334155">'
        f'OSM/Overpass reference: {len(line_features)} line segments, {len(station_features)} subway stations in bbox {BBOX}'
        '</text>'
    )
    elements.append("</svg>")
    SVG_FILE.write_text("\n".join(elements), encoding="utf-8")


def main() -> None:
    raw = fetch_existing_metro()
    line_features, station_features = parse_reference(raw)
    write_svg(line_features, station_features)

    line_names = sorted({f["properties"].get("line_name", "") for f in line_features if f["properties"].get("line_name")})
    print("Existing metro reference complete")
    print(f"  raw elements: {len(raw.get('elements', []))}")
    print(f"  line segments: {len(line_features)}")
    print(f"  subway stations: {len(station_features)}")
    print(f"  named lines: {', '.join(line_names[:20])}")
    print(f"  svg: {SVG_FILE}")


if __name__ == "__main__":
    main()
