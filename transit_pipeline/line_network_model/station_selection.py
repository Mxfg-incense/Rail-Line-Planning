"""Candidate station selection for the line-network model."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans

from line_model_config import (
    BBOX,
    CACHE_DIR,
    CENTER_MERGE_RADIUS_M,
    COMMERCIAL_CENTER_VALUE,
    DEMAND_DIR,
    JOB_CENTER_COUNT,
    OUTPUT_DIR,
    PIPELINE_DIR,
    POPULATION_CENTER_COUNT,
    TRANSPORT_CENTER_FIXED_VALUES,
    TRANSPORT_CENTERS_REQUIRED,
    TRANSPORT_CENTER_NAME_ALIASES,
    TRANSPORT_HUB_VALUES,
    WORKSPACE_DIR,
)


LEGACY_INPUT_DIR = PIPELINE_DIR / "output" / "openhouse_steps"
POPULATION_FILE = DEMAND_DIR / "04_population_points.geojson"
WORK_DESTINATIONS_FILE = DEMAND_DIR / "06_work_destinations.geojson"
FUNCTION_FILE = DEMAND_DIR / "05_function_categories.geojson"
ACTIVITY_ATTRACTIONS_FILE = DEMAND_DIR / "07_activity_attractions.geojson"
LEGACY_POPULATION_FILE = LEGACY_INPUT_DIR / "04_population_points.geojson"
LEGACY_WORK_DESTINATIONS_FILE = LEGACY_INPUT_DIR / "07_work_destinations.geojson"
LEGACY_FUNCTION_FILE = LEGACY_INPUT_DIR / "06_function_categories.geojson"
STATION_FILE = OUTPUT_DIR / "01_candidate_stations.geojson"
STATION_TABLE_FILE = OUTPUT_DIR / "01_candidate_stations_table.csv"
STATION_SELECTION_SVG = OUTPUT_DIR / "01_station_selection.svg"
POPULATION_CENTERS_FILE = OUTPUT_DIR / "01a_population_centers.geojson"
JOB_CENTERS_FILE = OUTPUT_DIR / "01b_job_centers.geojson"
TRANSPORT_CENTERS_FILE = OUTPUT_DIR / "01c_transport_centers.geojson"
COMMERCIAL_CENTERS_FILE = OUTPUT_DIR / "01d_commercial_centers.geojson"
POPULATION_CENTERS_SVG = OUTPUT_DIR / "01a_population_centers.svg"
JOB_CENTERS_SVG = OUTPUT_DIR / "01b_job_centers.svg"
TRANSPORT_CENTERS_SVG = OUTPUT_DIR / "01c_transport_centers.svg"
COMMERCIAL_CENTERS_SVG = OUTPUT_DIR / "01d_commercial_centers.svg"
ROAD_SOURCE_FILES = [
    DEMAND_DIR / "01_raw_overpass.json",
    CACHE_DIR / f"overpass_{BBOX[0]}_{BBOX[1]}_{BBOX[2]}_{BBOX[3]}.json",
    LEGACY_INPUT_DIR / "01_raw_overpass.json",
    WORKSPACE_DIR / "cache" / "openhouse_populator" / "shanghai_study_area_overpass.json",
]

WALK_RADIUS_M = 800.0
MIN_STATION_DISTANCE_M = CENTER_MERGE_RADIUS_M
HEATMAP_COLS = 72
HEATMAP_ROWS = 52

def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = a
    lon2, lat2 = b
    lat0 = math.radians((lat1 + lat2) / 2.0)
    dx = (lon2 - lon1) * 111_320.0 * math.cos(lat0)
    dy = (lat2 - lat1) * 110_540.0
    return math.hypot(dx, dy)


def existing_path(primary: Path, fallback: Path | None = None) -> Path:
    if primary.exists():
        return primary
    if fallback and fallback.exists():
        return fallback
    raise FileNotFoundError(
        f"{primary} not found. Run: uv run python .\\transit_pipeline\\line_network_model\\00_prepare_demand_data.py"
    )


def load_population() -> list[dict]:
    raw = read_json(existing_path(POPULATION_FILE, LEGACY_POPULATION_FILE))
    points = []
    for idx, feature in enumerate(raw["features"], start=1):
        lon, lat = feature["geometry"]["coordinates"]
        pop = float(feature["properties"].get("pop", 0.0))
        if pop <= 0:
            continue
        points.append({"id": f"R{idx:05d}", "lon": lon, "lat": lat, "pop": pop})
    return points


def load_jobs() -> list[dict]:
    file_path = WORK_DESTINATIONS_FILE if WORK_DESTINATIONS_FILE.exists() else LEGACY_WORK_DESTINATIONS_FILE
    if not file_path.exists():
        return []
    raw = read_json(file_path)
    points = []
    for feature in raw["features"]:
        lon, lat = feature["geometry"]["coordinates"]
        jobs = float(feature["properties"].get("jobs", 0.0))
        if jobs <= 0:
            continue
        points.append(
            {
                "id": feature["properties"].get("destination_id", ""),
                "lon": float(lon),
                "lat": float(lat),
                "jobs": jobs,
                "category": feature["properties"].get("category", ""),
                "name": feature["properties"].get("name", ""),
            }
        )
    return points


def demand_bounds(points: list[dict], margin: float = 0.02) -> tuple[float, float, float, float]:
    west = min(point["lon"] for point in points) - margin
    east = max(point["lon"] for point in points) + margin
    south = min(point["lat"] for point in points) - margin
    north = max(point["lat"] for point in points) + margin
    return west, south, east, north


def in_bounds(lon: float, lat: float, bounds: tuple[float, float, float, float]) -> bool:
    west, south, east, north = bounds
    return west <= lon <= east and south <= lat <= north


def load_activity_attractions(bounds: tuple[float, float, float, float]) -> list[dict]:
    raw = read_json(existing_path(ACTIVITY_ATTRACTIONS_FILE))
    attractions = []
    for idx, feature in enumerate(raw["features"], start=1):
        props = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"]
        lon = float(lon)
        lat = float(lat)
        if not in_bounds(lon, lat, bounds):
            continue
        attractions.append(
            {
                "id": props.get("attraction_id", f"A{idx:05d}"),
                "lon": lon,
                "lat": lat,
                "kind": props.get("kind", ""),
                "category": props.get("category", ""),
                "weight": float(props.get("weight", 0.0) or 0.0),
                "name": props.get("name", ""),
            }
        )
    return attractions


def load_commercial_hubs(attractions: list[dict]) -> list[dict]:
    hubs = []
    seen = set()
    for point in attractions:
        if point["kind"] != "commercial":
            continue
        key = (round(point["lon"], 5), round(point["lat"], 5))
        if key in seen:
            continue
        seen.add(key)
        hubs.append(
            {
                "lon": point["lon"],
                "lat": point["lat"],
                "name": point.get("name", ""),
                "source": "prepared_large_commercial_center",
                "hub_type": "commercial_hub",
                "fixed_value": COMMERCIAL_CENTER_VALUE,
            }
        )
    return hubs


def load_station_file() -> list[dict]:
    raw = read_json(STATION_FILE)
    stations = []
    for feature in raw["features"]:
        lon, lat = feature["geometry"]["coordinates"]
        props = feature["properties"]
        stations.append(
            {
                "station_id": props["station_id"],
                "lon": float(lon),
                "lat": float(lat),
                "station_type": props.get("station_type", ""),
                "required": bool(props.get("required", False)),
                "covered_pop": float(props.get("covered_pop", 0.0)),
                "covered_jobs": float(props.get("covered_jobs", 0.0)),
                "covered_commercial": float(props.get("covered_commercial", 0.0)),
                "covered_tourism": float(props.get("covered_tourism", 0.0)),
                "score": float(props.get("score", 0.0)),
                "population_value": float(props.get("population_value", 0.0)),
                "job_value": float(props.get("job_value", 0.0)),
                "transport_value": float(props.get("transport_value", 0.0)),
                "commercial_value": float(props.get("commercial_value", 0.0)),
                "total_value": float(props.get("total_value", props.get("score", 0.0))),
                "layers": props.get("layers", ""),
                "sources": props.get("sources", ""),
                "component_count": int(props.get("component_count", 1)),
                "name": props.get("name", ""),
            }
        )
    return stations


def way_coords(element: dict, nodes: dict[int, tuple[float, float]]) -> list[tuple[float, float]]:
    return [nodes[node_id] for node_id in element.get("nodes", []) if node_id in nodes]


def load_roads() -> list[dict]:
    source = next((path for path in ROAD_SOURCE_FILES if path.exists()), None)
    if source is None:
        return []

    raw = read_json(source)
    nodes = {
        el["id"]: (float(el["lon"]), float(el["lat"]))
        for el in raw.get("elements", [])
        if el.get("type") == "node" and "lon" in el and "lat" in el
    }

    roads = []
    for element in raw.get("elements", []):
        tags = element.get("tags", {})
        highway = tags.get("highway")
        if element.get("type") != "way" or not highway:
            continue
        coords = way_coords(element, nodes)
        if len(coords) < 2:
            continue
        roads.append({"coords": coords, "highway": highway})
    return roads


def road_style(highway: str) -> tuple[str, float, float]:
    major = {"motorway", "trunk", "primary"}
    medium = {"secondary", "tertiary", "unclassified"}
    if highway in major or highway.endswith("_link"):
        return "#94a3b8", 1.6, 0.55
    if highway in medium:
        return "#cbd5e1", 1.0, 0.55
    return "#e2e8f0", 0.55, 0.45


def canonical_transport_center_name(name: str) -> str | None:
    clean_name = name.strip()
    if not clean_name:
        return None
    if clean_name in TRANSPORT_CENTER_NAME_ALIASES:
        return TRANSPORT_CENTER_NAME_ALIASES[clean_name]
    for alias, canonical in TRANSPORT_CENTER_NAME_ALIASES.items():
        if alias and alias in clean_name:
            return canonical
    return None


def load_transport_hubs() -> list[dict]:
    raw = read_json(existing_path(FUNCTION_FILE, LEGACY_FUNCTION_FILE))
    hubs_by_name = {}
    for feature in raw["features"]:
        props = feature["properties"]
        tags = " ".join(
            str(props.get(key, ""))
            for key in ("building", "amenity", "public_transport", "railway", "aeroway", "other_tags", "name")
        ).lower()
        is_rail_hub = (
            "train_station" in tags
            or "station" in tags and "railway" in tags
            or "subway_entrance" in tags
        )
        aeroway = props.get("aeroway", "")
        name = str(props.get("name", ""))
        is_airport_hub = (
            aeroway in {"aerodrome", "terminal"}
            or ("airport" in tags and ("terminal" in tags or "aeroway" in tags))
            or ("机场" in name and ("航站楼" in name or aeroway in {"aerodrome", "terminal"}))
        )
        canonical_name = canonical_transport_center_name(name)
        if canonical_name is None or not (is_rail_hub or is_airport_hub):
            continue

        lon, lat = feature["geometry"]["coordinates"]
        hub_type = "airport_hub" if is_airport_hub else "rail_hub"
        candidate = {
            "lon": float(lon),
            "lat": float(lat),
            "name": canonical_name,
            "source": hub_type,
            "hub_type": hub_type,
            "fixed_value": TRANSPORT_CENTER_FIXED_VALUES[canonical_name],
            "raw_name": name,
        }
        existing = hubs_by_name.get(canonical_name)
        if existing is None or candidate["fixed_value"] > existing["fixed_value"]:
            hubs_by_name[canonical_name] = candidate
    return [hubs_by_name[name] for name in TRANSPORT_CENTER_FIXED_VALUES if name in hubs_by_name]


def covered_population(
    lon: float, lat: float, population: list[dict], radius_m: float = WALK_RADIUS_M
) -> float:
    total = 0.0
    for point in population:
        d = distance_m((lon, lat), (point["lon"], point["lat"]))
        if d <= radius_m:
            total += point["pop"]
    return total


def covered_jobs(
    lon: float, lat: float, jobs: list[dict], radius_m: float = WALK_RADIUS_M
) -> float:
    total = 0.0
    for point in jobs:
        d = distance_m((lon, lat), (point["lon"], point["lat"]))
        if d <= radius_m:
            total += point["jobs"]
    return total


def covered_attraction(
    lon: float,
    lat: float,
    attractions: list[dict],
    kind: str,
    radius_m: float = WALK_RADIUS_M,
) -> float:
    total = 0.0
    for point in attractions:
        if point["kind"] != kind:
            continue
        d = distance_m((lon, lat), (point["lon"], point["lat"]))
        if d <= radius_m:
            total += point["weight"]
    return total


def far_enough(candidate: dict, selected: list[dict]) -> bool:
    return all(
        distance_m((candidate["lon"], candidate["lat"]), (station["lon"], station["lat"]))
        > MIN_STATION_DISTANCE_M
        for station in selected
    )


def layer_center(
    lon: float,
    lat: float,
    layer: str,
    value: float,
    name: str = "",
    source: str = "",
) -> dict:
    return {
        "lon": float(lon),
        "lat": float(lat),
        "layer": layer,
        "name": name,
        "source": source,
        "population_value": value if layer == "population" else 0.0,
        "job_value": value if layer == "job" else 0.0,
        "transport_value": value if layer == "transport" else 0.0,
        "commercial_value": value if layer == "commercial" else 0.0,
        "value": float(value),
    }


def weighted_cluster_centers(points: list[dict], n_clusters: int, layer: str) -> list[dict]:
    if not points:
        return []
    coords = np.array([[p["lon"], p["lat"]] for p in points])
    weights = np.array([p["weight"] for p in points], dtype=float)
    n_clusters = min(n_clusters, len(points))
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(coords, sample_weight=weights)

    centers = []
    for cluster_idx, (lon, lat) in enumerate(kmeans.cluster_centers_):
        value = float(weights[labels == cluster_idx].sum())
        centers.append(layer_center(float(lon), float(lat), layer, value, source="weighted_kmeans"))
    centers.sort(key=lambda item: item["value"], reverse=True)
    return centers


def build_population_centers(population: list[dict]) -> list[dict]:
    points = [{"lon": p["lon"], "lat": p["lat"], "weight": p["pop"]} for p in population]
    return weighted_cluster_centers(points, POPULATION_CENTER_COUNT, "population")


def build_job_centers(jobs: list[dict]) -> list[dict]:
    points = [{"lon": p["lon"], "lat": p["lat"], "weight": p["jobs"]} for p in jobs]
    return weighted_cluster_centers(points, JOB_CENTER_COUNT, "job")


def build_transport_centers(hubs: list[dict]) -> list[dict]:
    return [
        layer_center(
            hub["lon"],
            hub["lat"],
            "transport",
            float(hub.get("fixed_value", TRANSPORT_HUB_VALUES.get(hub.get("hub_type", "rail_hub"), 0.0))),
            name=hub.get("name", ""),
            source=hub.get("hub_type", "rail_hub"),
        )
        for hub in hubs
    ]


def build_commercial_centers(commercial_hubs: list[dict]) -> list[dict]:
    return [
        layer_center(
            hub["lon"],
            hub["lat"],
            "commercial",
            float(hub.get("fixed_value", COMMERCIAL_CENTER_VALUE)),
            name=hub.get("name", ""),
            source="large_commercial_center",
        )
        for hub in commercial_hubs
    ]


def center_total_value(center: dict) -> float:
    return (
        float(center.get("population_value", 0.0))
        + float(center.get("job_value", 0.0))
        + float(center.get("transport_value", 0.0))
        + float(center.get("commercial_value", 0.0))
    )


def merge_center_group(group: list[dict]) -> dict:
    weights = [max(center_total_value(center), 1.0) for center in group]
    total_weight = sum(weights)
    lon = sum(center["lon"] * weight for center, weight in zip(group, weights)) / total_weight
    lat = sum(center["lat"] * weight for center, weight in zip(group, weights)) / total_weight
    layer_set = set()
    source_set = set()
    for center in group:
        if center.get("layer"):
            layer_set.add(center["layer"])
        for layer in str(center.get("layers", "")).split(","):
            if layer:
                layer_set.add(layer)
        if center.get("source"):
            source_set.add(center["source"])
        for source in str(center.get("sources", "")).split(","):
            if source:
                source_set.add(source)
    layers = sorted(layer_set)
    sources = sorted(source_set)
    names = [center.get("name", "") for center in group if center.get("name")]
    pop_value = sum(float(center.get("population_value", 0.0)) for center in group)
    job_value = sum(float(center.get("job_value", 0.0)) for center in group)
    transport_value = sum(float(center.get("transport_value", 0.0)) for center in group)
    commercial_value = sum(float(center.get("commercial_value", 0.0)) for center in group)
    total_value = pop_value + job_value + transport_value + commercial_value
    required_layers = {"transport"} if TRANSPORT_CENTERS_REQUIRED else set()
    station_type = "+".join(layers) + "_center"
    return {
        "lon": lon,
        "lat": lat,
        "station_type": station_type,
        "required": bool(required_layers.intersection(layers)),
        "layers": ",".join(layers),
        "sources": ",".join(sources),
        "component_count": sum(int(center.get("component_count", 1)) for center in group),
        "population_value": pop_value,
        "job_value": job_value,
        "transport_value": transport_value,
        "commercial_value": commercial_value,
        "total_value": total_value,
        "covered_pop": pop_value,
        "covered_jobs": job_value,
        "covered_commercial": commercial_value,
        "covered_tourism": 0.0,
        "score": total_value,
        "name": " / ".join(names),
    }


def merge_centers_to_candidates(centers: list[dict]) -> list[dict]:
    remaining = sorted(centers, key=center_total_value, reverse=True)
    merged = []
    while remaining:
        seed = remaining.pop(0)
        group = [seed]
        keep = []
        for center in remaining:
            if distance_m((seed["lon"], seed["lat"]), (center["lon"], center["lat"])) <= CENTER_MERGE_RADIUS_M:
                group.append(center)
            else:
                keep.append(center)
        remaining = keep
        merged.append(merge_center_group(group))

    changed = True
    while changed:
        changed = False
        next_round = []
        used = set()
        for idx, seed in enumerate(merged):
            if idx in used:
                continue
            group = [seed]
            used.add(idx)
            for other_idx, other in enumerate(merged):
                if other_idx in used:
                    continue
                if distance_m((seed["lon"], seed["lat"]), (other["lon"], other["lat"])) <= CENTER_MERGE_RADIUS_M:
                    group.append(other)
                    used.add(other_idx)
                    changed = True
            next_round.append(merge_center_group(group) if len(group) > 1 else seed)
        merged = next_round

    merged.sort(key=lambda item: item["total_value"], reverse=True)
    for idx, station in enumerate(merged, start=1):
        station["station_id"] = f"S{idx:03d}"
    return merged


def build_layer_centers(
    population: list[dict],
    jobs: list[dict],
    transport_hubs: list[dict],
    commercial_hubs: list[dict],
) -> dict[str, list[dict]]:
    return {
        "population": build_population_centers(population),
        "job": build_job_centers(jobs),
        "transport": build_transport_centers(transport_hubs),
        "commercial": build_commercial_centers(commercial_hubs),
    }


def select_candidate_stations_from_centers(layer_centers: dict[str, list[dict]]) -> list[dict]:
    centers = []
    for items in layer_centers.values():
        centers.extend(items)
    return merge_centers_to_candidates(centers)


def heatmap_demand_points(population: list[dict], jobs: list[dict], attractions: list[dict]) -> list[dict]:
    max_pop = max((point["pop"] for point in population), default=1.0)
    max_jobs = max((point["jobs"] for point in jobs), default=1.0)
    max_commercial = max((point["weight"] for point in attractions if point["kind"] == "commercial"), default=1.0)
    max_tourism = max((point["weight"] for point in attractions if point["kind"] == "tourism"), default=1.0)
    points = []
    for point in population:
        points.append({"lon": point["lon"], "lat": point["lat"], "weight": point["pop"] / max_pop})
    for point in jobs:
        points.append({"lon": point["lon"], "lat": point["lat"], "weight": point["jobs"] / max_jobs})
    for point in attractions:
        max_value = max_commercial if point["kind"] == "commercial" else max_tourism
        points.append({"lon": point["lon"], "lat": point["lat"], "weight": point["weight"] / max_value})
    return points


def select_candidate_stations(
    population: list[dict],
    transport_hubs: list[dict],
    jobs: list[dict],
    commercial_hubs: list[dict],
) -> list[dict]:
    layer_centers = build_layer_centers(population, jobs, transport_hubs, commercial_hubs)
    return select_candidate_stations_from_centers(layer_centers)


def write_station_geojson(stations: list[dict]) -> None:
    features = []
    for station in stations:
        props = dict(station)
        lon = props.pop("lon")
        lat = props.pop("lat")
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": props,
            }
        )
    write_json(STATION_FILE, {"type": "FeatureCollection", "features": features})


def write_station_table(stations: list[dict]) -> None:
    fieldnames = [
        "station_id",
        "lon",
        "lat",
        "layers",
        "sources",
        "required",
        "component_count",
        "population_value",
        "job_value",
        "transport_value",
        "commercial_value",
        "total_value",
        "name",
        "station_type",
    ]
    with STATION_TABLE_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for station in stations:
            writer.writerow(
                {
                    "station_id": station.get("station_id", ""),
                    "lon": f'{station.get("lon", 0.0):.8f}',
                    "lat": f'{station.get("lat", 0.0):.8f}',
                    "layers": station.get("layers", ""),
                    "sources": station.get("sources", ""),
                    "required": station.get("required", False),
                    "component_count": station.get("component_count", 1),
                    "population_value": f'{station.get("population_value", 0.0):.2f}',
                    "job_value": f'{station.get("job_value", 0.0):.2f}',
                    "transport_value": f'{station.get("transport_value", 0.0):.2f}',
                    "commercial_value": f'{station.get("commercial_value", 0.0):.2f}',
                    "total_value": f'{station.get("total_value", station.get("score", 0.0)):.2f}',
                    "name": station.get("name", ""),
                    "station_type": station.get("station_type", ""),
                }
            )


def write_center_geojson(path: Path, centers: list[dict]) -> None:
    features = []
    for idx, center in enumerate(centers, start=1):
        props = dict(center)
        lon = props.pop("lon")
        lat = props.pop("lat")
        props["center_id"] = f'{center["layer"].upper()}_{idx:03d}'
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": props,
            }
        )
    write_json(path, {"type": "FeatureCollection", "features": features})


def write_layer_center_geojsons(layer_centers: dict[str, list[dict]]) -> None:
    write_center_geojson(POPULATION_CENTERS_FILE, layer_centers["population"])
    write_center_geojson(JOB_CENTERS_FILE, layer_centers["job"])
    write_center_geojson(TRANSPORT_CENTERS_FILE, layer_centers["transport"])
    write_center_geojson(COMMERCIAL_CENTERS_FILE, layer_centers["commercial"])


def heat_color(value: float) -> tuple[str, float]:
    if value <= 0:
        return "#ffffff", 0.0
    stops = [
        (0.00, (254, 240, 138)),
        (0.35, (251, 146, 60)),
        (0.70, (239, 68, 68)),
        (1.00, (127, 29, 29)),
    ]
    for (lo_t, lo_rgb), (hi_t, hi_rgb) in zip(stops, stops[1:]):
        if value <= hi_t:
            ratio = (value - lo_t) / max(hi_t - lo_t, 1e-9)
            rgb = tuple(round(lo + (hi - lo) * ratio) for lo, hi in zip(lo_rgb, hi_rgb))
            return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}", 0.16 + 0.58 * value
    return "#7f1d1d", 0.74


def value_color(value: float, max_value: float) -> str:
    ratio = min(max(value / max(max_value, 1e-9), 0.0), 1.0)
    stops = [
        (0.00, (203, 213, 225)),
        (0.35, (56, 189, 248)),
        (0.70, (37, 99, 235)),
        (1.00, (124, 58, 237)),
    ]
    for (lo_t, lo_rgb), (hi_t, hi_rgb) in zip(stops, stops[1:]):
        if ratio <= hi_t:
            blend = (ratio - lo_t) / max(hi_t - lo_t, 1e-9)
            rgb = tuple(round(lo + (hi - lo) * blend) for lo, hi in zip(lo_rgb, hi_rgb))
            return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    return "#7c3aed"


def map_bounds(
    population: list[dict],
    jobs: list[dict],
    centers: list[dict],
    roads: list[dict],
) -> tuple[float, float, float, float]:
    lons = [p["lon"] for p in population] + [j["lon"] for j in jobs] + [c["lon"] for c in centers]
    lats = [p["lat"] for p in population] + [j["lat"] for j in jobs] + [c["lat"] for c in centers]
    for road in roads:
        for lon, lat in road["coords"]:
            lons.append(lon)
            lats.append(lat)
    return min(lons), min(lats), max(lons), max(lats)


def write_center_svg(
    path: Path,
    title: str,
    centers: list[dict],
    population: list[dict],
    jobs: list[dict],
    roads: list[dict],
    center_fill: str,
) -> None:
    roads = roads or []
    width, height, pad = 1100, 850, 45
    west, south, east, north = map_bounds(population, jobs, centers, roads)
    max_center_value = max((center_total_value(center) for center in centers), default=1.0)

    def project(lon: float, lat: float) -> tuple[float, float]:
        x = pad + (lon - west) / max(east - west, 1e-9) * (width - 2 * pad)
        y = height - pad - (lat - south) / max(north - south, 1e-9) * (height - 2 * pad)
        return x, y

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        f'<text x="45" y="34" font-family="Arial" font-size="22" font-weight="700" fill="#0f172a">{title}</text>',
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

    elements.append('<g id="population-context">')
    max_pop = max((point["pop"] for point in population), default=1.0)
    for point in population:
        x, y = project(point["lon"], point["lat"])
        r = 0.45 + 1.2 * math.sqrt(point["pop"] / max_pop)
        elements.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="#475569" opacity="0.12"/>')
    elements.append("</g>")

    elements.append('<g id="job-context">')
    max_jobs = max((point["jobs"] for point in jobs), default=1.0)
    for point in jobs:
        x, y = project(point["lon"], point["lat"])
        r = 0.5 + 1.6 * math.sqrt(point["jobs"] / max_jobs)
        elements.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="#16a34a" opacity="0.12"/>')
    elements.append("</g>")

    elements.append('<g id="layer-centers">')
    for idx, center in enumerate(centers, start=1):
        x, y = project(center["lon"], center["lat"])
        value = center_total_value(center)
        r = 5.0 + 12.0 * math.sqrt(value / max(max_center_value, 1e-9))
        fill = value_color(value, max_center_value) if center_fill == "value" else center_fill
        elements.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="{fill}" stroke="#ffffff" stroke-width="2" opacity="0.92"/>')
        label = center.get("name") or f"C{idx:02d}"
        elements.append(
            f'<text x="{x + r + 3:.1f}" y="{y - 4:.1f}" font-family="Arial" font-size="10" '
            f'font-weight="700" fill="#0f172a">{label}</text>'
        )
    elements.append("</g>")
    elements.append(
        '<text x="45" y="815" font-family="Arial" font-size="13" fill="#334155">'
        'circle size and color indicate center value; road network and demand points are context layers'
        '</text>'
    )
    elements.append("</svg>")
    path.write_text("\n".join(elements), encoding="utf-8")


def write_layer_center_svgs(
    layer_centers: dict[str, list[dict]],
    population: list[dict],
    jobs: list[dict],
    roads: list[dict],
) -> None:
    write_center_svg(POPULATION_CENTERS_SVG, "Layer 1: Population Centers", layer_centers["population"], population, jobs, roads, "value")
    write_center_svg(JOB_CENTERS_SVG, "Layer 2: Job Centers", layer_centers["job"], population, jobs, roads, "value")
    write_center_svg(TRANSPORT_CENTERS_SVG, "Layer 3: Fixed Transport Centers", layer_centers["transport"], population, jobs, roads, "#dc2626")
    write_center_svg(COMMERCIAL_CENTERS_SVG, "Layer 4: Fixed Commercial Centers", layer_centers["commercial"], population, jobs, roads, "#f97316")


def write_station_selection_svg(
    population: list[dict],
    jobs: list[dict],
    attractions: list[dict],
    stations: list[dict],
    roads: list[dict] | None = None,
) -> None:
    roads = roads or []
    width, height, pad = 1100, 850, 45
    lons = (
        [p["lon"] for p in population]
        + [j["lon"] for j in jobs]
        + [a["lon"] for a in attractions]
        + [s["lon"] for s in stations]
    )
    lats = (
        [p["lat"] for p in population]
        + [j["lat"] for j in jobs]
        + [a["lat"] for a in attractions]
        + [s["lat"] for s in stations]
    )
    for road in roads:
        for lon, lat in road["coords"]:
            lons.append(lon)
            lats.append(lat)
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)
    max_pop = max(p["pop"] for p in population)
    max_jobs = max((j["jobs"] for j in jobs), default=1.0)
    max_commercial = max((a["weight"] for a in attractions if a["kind"] == "commercial"), default=1.0)
    max_tourism = max((a["weight"] for a in attractions if a["kind"] == "tourism"), default=1.0)
    max_station_value = max((s.get("total_value", s.get("score", 0.0)) for s in stations), default=1.0)
    grid = [[0.0 for _ in range(HEATMAP_COLS)] for _ in range(HEATMAP_ROWS)]
    for point in heatmap_demand_points(population, jobs, attractions):
        col = min(
            HEATMAP_COLS - 1,
            max(0, int((point["lon"] - west) / max(east - west, 1e-9) * HEATMAP_COLS)),
        )
        row = min(
            HEATMAP_ROWS - 1,
            max(0, int((north - point["lat"]) / max(north - south, 1e-9) * HEATMAP_ROWS)),
        )
        grid[row][col] += point["weight"]
    max_cell = max(max(row) for row in grid)

    def project(lon: float, lat: float) -> tuple[float, float]:
        x = pad + (lon - west) / max(east - west, 1e-9) * (width - 2 * pad)
        y = height - pad - (lat - south) / max(north - south, 1e-9) * (height - 2 * pad)
        return x, y

    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    cell_w = inner_w / HEATMAP_COLS
    cell_h = inner_h / HEATMAP_ROWS

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        '<text x="45" y="34" font-family="Arial" font-size="22" font-weight="700" fill="#0f172a">Candidate Station Selection: Residents + Jobs + Activities</text>',
        '<g id="demand-heatmap">',
    ]

    for row_idx, row in enumerate(grid):
        for col_idx, value in enumerate(row):
            if value <= 0:
                continue
            norm = math.sqrt(value / max(max_cell, 1e-9))
            color, opacity = heat_color(norm)
            x = pad + col_idx * cell_w
            y = pad + row_idx * cell_h
            elements.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w + 0.4:.1f}" height="{cell_h + 0.4:.1f}" '
                f'fill="{color}" opacity="{opacity:.3f}"/>'
            )
    elements.extend(
        [
            "</g>",
            '<g id="road-basemap">',
        ]
    )

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

    elements.extend(
        [
            "</g>",
        '<g id="population-points">',
        ]
    )

    for point in population:
        x, y = project(point["lon"], point["lat"])
        r = 0.45 + 1.2 * math.sqrt(point["pop"] / max_pop)
        elements.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="#334155" opacity="0.18"/>'
        )
    elements.append("</g>")

    elements.append('<g id="job-points">')
    for point in jobs:
        x, y = project(point["lon"], point["lat"])
        r = 0.7 + 2.6 * math.sqrt(point["jobs"] / max_jobs)
        elements.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="#16a34a" opacity="0.30"/>'
        )
    elements.append("</g>")

    elements.append('<g id="commercial-points">')
    for point in attractions:
        if point["kind"] != "commercial":
            continue
        x, y = project(point["lon"], point["lat"])
        r = 0.7 + 2.4 * math.sqrt(point["weight"] / max_commercial)
        elements.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="#f97316" opacity="0.32"/>'
        )
    elements.append("</g>")

    elements.append('<g id="tourism-points">')
    for point in attractions:
        if point["kind"] != "tourism":
            continue
        x, y = project(point["lon"], point["lat"])
        r = 0.8 + 2.8 * math.sqrt(point["weight"] / max_tourism)
        elements.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="#a855f7" opacity="0.34"/>'
        )
    elements.append("</g>")

    elements.append('<g id="candidate-stations">')
    for station in stations:
        x, y = project(station["lon"], station["lat"])
        value = float(station.get("total_value", station.get("score", 0.0)))
        fill = value_color(value, max_station_value)
        r = 5.5 + 8.5 * math.sqrt(value / max(max_station_value, 1e-9))
        elements.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="{fill}" stroke="#ffffff" stroke-width="2"/>'
        )
        elements.append(
            f'<text x="{x + r + 3:.1f}" y="{y - 8:.1f}" font-family="Arial" font-size="11" '
            f'font-weight="700" fill="#0f172a">{station["station_id"]}</text>'
        )
    elements.append("</g>")
    elements.append(
        '<text x="45" y="815" font-family="Arial" font-size="13" fill="#334155">'
        'final V: centers within 1 km are merged; station circle size and color indicate summed value'
        '</text>'
    )
    elements.append("</svg>")
    STATION_SELECTION_SVG.write_text("\n".join(elements), encoding="utf-8")


def minimum_station_distance(stations: list[dict]) -> float:
    best = float("inf")
    for i, station in enumerate(stations):
        for other in stations[i + 1 :]:
            d = distance_m((station["lon"], station["lat"]), (other["lon"], other["lat"]))
            best = min(best, d)
    return best
