#!/usr/bin/env python3
"""
Generate an OpenHousePopulator-style population distribution for the
Shanghai study area used by full_pipeline_v2.py.

This ports the repository's simple heuristic:
OSM buildings + house numbers + building levels -> estimated flats -> population.
It intentionally uses only the Python standard library so it can run in this
workspace without Rust/Cargo or geospatial Python packages.
"""

from __future__ import annotations

import json
import math
import random
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "transit_pipeline" / "output"
CACHE_DIR = ROOT / "cache" / "openhouse_populator"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Same target area as transit_pipeline/full_pipeline_v2.py:
# west, south, east, north around Lujiazui - Century Park, Pudong.
BBOX = (121.48, 31.20, 121.56, 31.26)
TOTAL_POPULATION = 500_000

SETTINGS = {
    "reroll_threshold": 90,
    "reroll_probability": 2,
    "level_factor": 1,
    "housenumber_factor": 2,
    "exclude_landuse": {"allotments", "commercial", "industrial", "military", "retail"},
    "exclude_tags": {"amenity", "leisure"},
    "single_home_list": {"house", "detached"},
    "apartment_list": {"apartments", "residential"},
    "unspecified_list": {"terrace", "semidetached_house"},
}


def overpass_query() -> dict:
    west, south, east, north = BBOX
    bbox = f"{south},{west},{north},{east}"
    query = f"""
    [out:json][timeout:180];
    (
      way["building"]({bbox});
      relation["building"]({bbox});
      node["addr:housenumber"]({bbox});
      way["landuse"~"^(allotments|commercial|industrial|military|retail)$"]({bbox});
      relation["landuse"~"^(allotments|commercial|industrial|military|retail)$"]({bbox});
      way["amenity"]({bbox});
      relation["amenity"]({bbox});
      way["leisure"]({bbox});
      relation["leisure"]({bbox});
    );
    out body;
    >;
    out skel qt;
    """
    cache_path = CACHE_DIR / "shanghai_study_area_overpass.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    request = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=data,
        headers={"User-Agent": "CS240 OpenHousePopulator heuristic demo"},
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        payload = response.read().decode("utf-8")
    cache_path.write_text(payload, encoding="utf-8")
    return json.loads(payload)


def lonlat_to_m(lon: float, lat: float) -> tuple[float, float]:
    lat0 = math.radians((BBOX[1] + BBOX[3]) / 2)
    x = (lon - BBOX[0]) * 111_320 * math.cos(lat0)
    y = (lat - BBOX[1]) * 110_540
    return x, y


def polygon_area_m2(coords: list[tuple[float, float]]) -> float:
    pts = [lonlat_to_m(lon, lat) for lon, lat in coords]
    area = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def centroid(coords: list[tuple[float, float]]) -> tuple[float, float]:
    if not coords:
        return (0.0, 0.0)
    return (
        sum(lon for lon, _ in coords) / len(coords),
        sum(lat for _, lat in coords) / len(coords),
    )


def point_in_poly(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        crosses = (yi > y) != (yj > y)
        if crosses:
            x_intersect = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_intersect:
                inside = not inside
        j = i
    return inside


def parse_positive_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(0, int(float(value)))
    except ValueError:
        return None


def estimate_flats(tags: dict, house_number_count: int) -> int:
    explicit = parse_positive_int(tags.get("building:flats"))
    if explicit is not None:
        return explicit

    building = tags.get("building", "yes")
    if building in SETTINGS["single_home_list"]:
        flats = 1
    elif building in SETTINGS["apartment_list"] or building in SETTINGS["unspecified_list"]:
        flats = house_number_count * SETTINGS["housenumber_factor"] if house_number_count else 4
    elif building == "yes" and house_number_count:
        flats = house_number_count
    else:
        flats = 0

    levels = parse_positive_int(tags.get("building:levels"))
    if levels:
        flats *= levels * SETTINGS["level_factor"]
    return flats


def extract_way_polygon(element: dict, nodes: dict[int, tuple[float, float]]) -> list[tuple[float, float]]:
    refs = element.get("nodes", [])
    coords = [nodes[n] for n in refs if n in nodes]
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return coords


def should_exclude(tags: dict) -> bool:
    if tags.get("landuse") in SETTINGS["exclude_landuse"]:
        return True
    return any(tag in tags for tag in SETTINGS["exclude_tags"])


def build_population_features(raw: dict) -> list[dict]:
    nodes = {
        el["id"]: (el["lon"], el["lat"])
        for el in raw["elements"]
        if el["type"] == "node" and "lon" in el and "lat" in el
    }
    house_numbers = [
        (el["lon"], el["lat"])
        for el in raw["elements"]
        if el["type"] == "node" and "addr:housenumber" in el.get("tags", {})
    ]
    exclude_polys = []
    building_candidates = []
    for el in raw["elements"]:
        if el["type"] != "way":
            continue
        tags = el.get("tags", {})
        coords = extract_way_polygon(el, nodes)
        if len(coords) < 3:
            continue
        if should_exclude(tags):
            exclude_polys.append(coords)
        if "building" in tags:
            building_candidates.append((coords, tags))

    buildings = []
    for coords, tags in building_candidates:
        c = centroid(coords)
        if any(point_in_poly(c, poly) for poly in exclude_polys):
            continue
        area = polygon_area_m2(coords)
        if area <= 10:
            continue
        house_number_count = sum(1 for hn in house_numbers if point_in_poly(hn, coords))
        if "addr:housenumber" in tags:
            house_number_count = max(house_number_count, 1)
        flats = estimate_flats(tags, house_number_count)
        if flats <= 0:
            continue
        buildings.append({"coords": coords, "centroid": c, "flats": flats, "area": area, "pop": 0})

    rng = random.Random(42)
    total_flats = sum(b["flats"] for b in buildings)
    if total_flats <= 0:
        return []

    flat_population = [0] * total_flats
    for _ in range(TOTAL_POPULATION):
        flat_population[rng.randrange(total_flats)] += 1

    offset = 0
    for building in buildings:
        count = building["flats"]
        building["pop"] = sum(flat_population[offset : offset + count])
        offset += count

    return buildings


def write_geojson(buildings: list[dict]) -> Path:
    features = []
    for b in buildings:
        lon, lat = b["centroid"]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "pop": int(b["pop"]),
                    "flats": int(b["flats"]),
                    "building_area": round(b["area"], 2),
                },
            }
        )
    path = OUT_DIR / "openhouse_shanghai_population.geojson"
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def color_for_pop(pop: int, max_pop: int) -> str:
    t = math.sqrt(pop / max_pop) if max_pop else 0
    r = int(255)
    g = int(230 - 170 * t)
    b = int(170 - 150 * t)
    return f"rgb({r},{g},{b})"


def write_svg(buildings: list[dict]) -> Path:
    west, south, east, north = BBOX
    width, height = 1400, 1050
    max_pop = max((b["pop"] for b in buildings), default=1)

    def sx(lon: float) -> float:
        return (lon - west) / (east - west) * width

    def sy(lat: float) -> float:
        return height - (lat - south) / (north - south) * height

    sorted_buildings = sorted(buildings, key=lambda b: b["pop"])
    circles = []
    for b in sorted_buildings:
        lon, lat = b["centroid"]
        radius = 1.4 + 8.5 * math.sqrt(b["pop"] / max_pop)
        circles.append(
            f'<circle cx="{sx(lon):.2f}" cy="{sy(lat):.2f}" r="{radius:.2f}" '
            f'fill="{color_for_pop(b["pop"], max_pop)}" fill-opacity="0.58" '
            f'stroke="rgb(110,40,30)" stroke-opacity="0.18" stroke-width="0.35">'
            f'<title>pop={b["pop"]}, flats={b["flats"]}, area={b["area"]:.0f} m2</title></circle>'
        )

    total_pop = sum(b["pop"] for b in buildings)
    total_flats = sum(b["flats"] for b in buildings)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#f7f7f3"/>
<text x="36" y="48" font-family="Arial, sans-serif" font-size="28" font-weight="700" fill="#202020">Shanghai study area population distribution</text>
<text x="36" y="82" font-family="Arial, sans-serif" font-size="17" fill="#404040">OpenHousePopulator-style heuristic: OSM buildings + house numbers + levels -> flats -> {total_pop:,} residents</text>
<text x="36" y="108" font-family="Arial, sans-serif" font-size="15" fill="#606060">BBox {west}, {south}, {east}, {north}; buildings={len(buildings):,}; flats={total_flats:,}</text>
<g transform="translate(0,20)">
{chr(10).join(circles)}
</g>
<rect x="32" y="{height-108}" width="310" height="72" fill="white" fill-opacity="0.82" stroke="#cccccc"/>
<circle cx="58" cy="{height-82}" r="5" fill="rgb(255,210,150)" fill-opacity="0.7"/><text x="78" y="{height-76}" font-family="Arial, sans-serif" font-size="14" fill="#333">Lower estimated building population</text>
<circle cx="58" cy="{height-52}" r="11" fill="rgb(255,60,20)" fill-opacity="0.7"/><text x="78" y="{height-46}" font-family="Arial, sans-serif" font-size="14" fill="#333">Higher estimated building population</text>
</svg>
"""
    path = OUT_DIR / "openhouse_shanghai_population.svg"
    path.write_text(svg, encoding="utf-8")
    return path


def main() -> None:
    raw = overpass_query()
    buildings = build_population_features(raw)
    geojson = write_geojson(buildings)
    svg = write_svg(buildings)
    print(f"features={len(buildings)}")
    print(f"total_pop={sum(b['pop'] for b in buildings)}")
    print(f"total_flats={sum(b['flats'] for b in buildings)}")
    print(f"geojson={geojson}")
    print(f"svg={svg}")


if __name__ == "__main__":
    main()
