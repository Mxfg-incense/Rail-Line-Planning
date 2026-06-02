"""Demand-data preparation used by the line-network model.

This folds the useful OpenHouse-style preprocessing into this folder:
raw OSM -> population points -> function categories -> job destinations.
"""

from __future__ import annotations

import json
import math
import random
import urllib.parse
import urllib.request
from pathlib import Path

from line_network_model.line_model_config import BBOX, CACHE_DIR, DEMAND_DIR, TOTAL_POPULATION


RAW_FILE = DEMAND_DIR / "01_raw_overpass.json"
BUILDINGS_FILE = DEMAND_DIR / "02_buildings.geojson"
EXCLUSIONS_FILE = DEMAND_DIR / "02_exclusion_areas.geojson"
HOUSENUMBERS_FILE = DEMAND_DIR / "02_housenumbers.geojson"
BUILDING_FLATS_FILE = DEMAND_DIR / "03_building_flats.geojson"
POPULATION_FILE = DEMAND_DIR / "04_population_points.geojson"
FUNCTION_FILE = DEMAND_DIR / "05_function_categories.geojson"
WORK_DESTINATIONS_FILE = DEMAND_DIR / "06_work_destinations.geojson"
ACTIVITY_ATTRACTIONS_FILE = DEMAND_DIR / "07_activity_attractions.geojson"

SETTINGS = {
    "level_factor": 1,
    "housenumber_factor": 2,
    "exclude_landuse": {"allotments", "commercial", "industrial", "military", "retail"},
    "exclude_tags": {"amenity", "leisure"},
    "single_home_list": {"house", "detached"},
    "apartment_list": {"apartments", "residential"},
    "unspecified_list": {"terrace", "semidetached_house"},
}

CATEGORIES = {
    "residential": {
        "label": "Residential",
        "match": {"building": {"apartments", "residential", "house", "dormitory", "terrace", "semidetached_house"}},
    },
    "commercial_office": {
        "label": "Commercial / Office",
        "match": {"building": {"commercial", "office"}, "landuse": {"commercial"}, "office": "*"},
    },
    "retail_service": {
        "label": "Retail / Service",
        "match": {
            "building": {"retail"},
            "landuse": {"retail"},
            "shop": "*",
            "amenity": {"restaurant", "cafe", "bank", "marketplace", "fast_food", "bar", "cinema"},
        },
    },
    "industry": {
        "label": "Industrial",
        "match": {"building": {"industrial", "warehouse"}, "landuse": {"industrial"}},
    },
    "education": {
        "label": "Education",
        "match": {"building": {"school", "kindergarten", "college", "university"}, "amenity": {"school", "kindergarten", "college", "university", "library"}},
    },
    "healthcare": {
        "label": "Healthcare",
        "match": {"building": {"hospital"}, "amenity": {"hospital", "clinic", "doctors", "pharmacy"}},
    },
    "hotel_tourism": {
        "label": "Hotel / Tourism",
        "match": {"building": {"hotel"}, "tourism": "*"},
    },
    "leisure_park": {
        "label": "Leisure / Park",
        "match": {"leisure": "*"},
    },
    "transport_public": {
        "label": "Transport / Public",
        "match": {
            "building": {"train_station", "transportation", "fire_station"},
            "amenity": {"bus_station", "ferry_terminal", "fuel", "police", "fire_station", "courthouse"},
            "public_transport": "*",
            "railway": "*",
            "aeroway": {"aerodrome", "terminal"},
        },
    },
    "other": {"label": "Other", "match": {}},
}

PRIORITY = [
    "healthcare",
    "education",
    "transport_public",
    "hotel_tourism",
    "retail_service",
    "commercial_office",
    "industry",
    "leisure_park",
    "residential",
    "other",
]

FUNCTION_TAGS = {
    "building",
    "landuse",
    "amenity",
    "shop",
    "office",
    "tourism",
    "leisure",
    "public_transport",
    "railway",
    "aeroway",
}

WORK_CATEGORIES = {
    "commercial_office": {"default_levels": 6.0, "sqm_per_job": 25.0, "base_jobs": 8.0},
    "retail_service": {"default_levels": 2.0, "sqm_per_job": 35.0, "base_jobs": 6.0},
    "industry": {"default_levels": 2.0, "sqm_per_job": 70.0, "base_jobs": 8.0},
    "education": {"default_levels": 4.0, "sqm_per_job": 60.0, "base_jobs": 25.0},
    "healthcare": {"default_levels": 6.0, "sqm_per_job": 45.0, "base_jobs": 35.0},
    "hotel_tourism": {"default_levels": 8.0, "sqm_per_job": 55.0, "base_jobs": 20.0},
}

ACTIVITY_COMMERCIAL_CATEGORIES = {"retail_service"}
ACTIVITY_TOURISM_CATEGORIES = {"hotel_tourism", "leisure_park"}
COMMERCIAL_CENTER_SUFFIXES = ("广场", "购物中心")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def point_feature(lon: float, lat: float, properties: dict) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": properties}


def polygon_feature(coords: list[tuple[float, float]], properties: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[lon, lat] for lon, lat in coords + [coords[0]]]]},
        "properties": properties,
    }


def coords_from_feature(feature: dict) -> list[tuple[float, float]]:
    ring = feature["geometry"]["coordinates"][0]
    if ring and ring[0] == ring[-1]:
        ring = ring[:-1]
    return [(float(lon), float(lat)) for lon, lat in ring]


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
    return (sum(lon for lon, _ in coords) / len(coords), sum(lat for _, lat in coords) / len(coords))


def point_in_poly(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
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


def should_exclude(tags: dict) -> bool:
    if tags.get("landuse") in SETTINGS["exclude_landuse"]:
        return True
    return any(tag in tags for tag in SETTINGS["exclude_tags"])


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


def overpass_payload() -> str:
    west, south, east, north = BBOX
    bbox = f"{south},{west},{north},{east}"
    return f"""
    [out:json][timeout:180];
    (
      way["building"]({bbox});
      relation["building"]({bbox});
      way["highway"]({bbox});
      node["addr:housenumber"]({bbox});
      way["landuse"~"^(allotments|commercial|industrial|military|retail)$"]({bbox});
      relation["landuse"~"^(allotments|commercial|industrial|military|retail)$"]({bbox});
      way["amenity"]({bbox});
      relation["amenity"]({bbox});
      way["leisure"]({bbox});
      relation["leisure"]({bbox});
      node["amenity"]({bbox});
      node["shop"]({bbox});
      node["tourism"]({bbox});
      node["railway"]({bbox});
      node["public_transport"]({bbox});
      way["aeroway"~"^(aerodrome|terminal)$"]({bbox});
      relation["aeroway"~"^(aerodrome|terminal)$"]({bbox});
      node["aeroway"~"^(aerodrome|terminal)$"]({bbox});
    );
    out body;
    >;
    out skel qt;
    """


def fetch_overpass() -> dict:
    cache_file = CACHE_DIR / f"overpass_{BBOX[0]}_{BBOX[1]}_{BBOX[2]}_{BBOX[3]}.json"
    if cache_file.exists():
        raw = read_json(cache_file)
    else:
        data = urllib.parse.urlencode({"data": overpass_payload()}).encode("utf-8")
        request = urllib.request.Request(
            "https://overpass-api.de/api/interpreter",
            data=data,
            headers={"User-Agent": "CS240 line network model demand prep"},
        )
        with urllib.request.urlopen(request, timeout=240) as response:
            payload = response.read().decode("utf-8")
        cache_file.write_text(payload, encoding="utf-8")
        raw = json.loads(payload)
    try:
        write_json(RAW_FILE, raw)
    except OSError as exc:
        print(f"Warning: could not update {RAW_FILE}: {exc}")
    return raw


def way_coords(element: dict, nodes: dict[int, tuple[float, float]]) -> list[tuple[float, float]]:
    coords = [nodes[n] for n in element.get("nodes", []) if n in nodes]
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return coords


def extract_inputs(raw: dict) -> tuple[list[dict], list[dict], list[dict]]:
    nodes = {
        el["id"]: (el["lon"], el["lat"])
        for el in raw["elements"]
        if el["type"] == "node" and "lon" in el and "lat" in el
    }
    buildings = []
    exclusions = []
    housenumbers = []

    for el in raw["elements"]:
        tags = el.get("tags", {})
        if el["type"] == "node" and "addr:housenumber" in tags:
            housenumbers.append(point_feature(el["lon"], el["lat"], {"addr:housenumber": tags.get("addr:housenumber", "")}))
        if el["type"] != "way":
            continue
        coords = way_coords(el, nodes)
        if len(coords) < 3:
            continue
        if should_exclude(tags):
            exclusions.append(polygon_feature(coords, {"osm_id": el["id"], "landuse": tags.get("landuse", ""), "amenity": tags.get("amenity", ""), "leisure": tags.get("leisure", "")}))
        if "building" in tags:
            buildings.append(
                polygon_feature(
                    coords,
                    {
                        "osm_id": el["id"],
                        "building": tags.get("building", ""),
                        "building:levels": tags.get("building:levels", ""),
                        "building:flats": tags.get("building:flats", ""),
                        "addr:housenumber": tags.get("addr:housenumber", ""),
                        "area_m2": round(polygon_area_m2(coords), 2),
                    },
                )
            )

    write_json(BUILDINGS_FILE, feature_collection(buildings))
    write_json(EXCLUSIONS_FILE, feature_collection(exclusions))
    write_json(HOUSENUMBERS_FILE, feature_collection(housenumbers))
    return buildings, exclusions, housenumbers


def estimate_building_flats(buildings: list[dict], exclusions: list[dict], housenumbers: list[dict]) -> list[dict]:
    exclusion_polys = [coords_from_feature(f) for f in exclusions]
    housenumber_points = [tuple(f["geometry"]["coordinates"]) for f in housenumbers]
    output = []

    for feature in buildings:
        coords = coords_from_feature(feature)
        props = feature["properties"]
        c = centroid(coords)
        if any(point_in_poly(c, poly) for poly in exclusion_polys):
            continue
        area = polygon_area_m2(coords)
        if area <= 10:
            continue
        house_number_count = sum(1 for point in housenumber_points if point_in_poly(point, coords))
        if props.get("addr:housenumber"):
            house_number_count = max(house_number_count, 1)
        flats = estimate_flats(props, house_number_count)
        if flats <= 0:
            continue
        next_props = dict(props)
        next_props.update({"area_m2": round(area, 2), "centroid_lon": c[0], "centroid_lat": c[1], "housenumber_count": house_number_count, "flats": flats})
        output.append(polygon_feature(coords, next_props))

    write_json(BUILDING_FLATS_FILE, feature_collection(output))
    return output


def distribute_population(building_flats: list[dict]) -> list[dict]:
    total_flats = sum(int(f["properties"]["flats"]) for f in building_flats)
    if total_flats <= 0:
        raise RuntimeError("No residential flats found in study area.")

    rng = random.Random(42)
    flat_population = [0] * total_flats
    for _ in range(TOTAL_POPULATION):
        flat_population[rng.randrange(total_flats)] += 1

    features = []
    offset = 0
    for feature in building_flats:
        props = feature["properties"]
        flats = int(props["flats"])
        pop = sum(flat_population[offset : offset + flats])
        offset += flats
        features.append(
            point_feature(
                float(props["centroid_lon"]),
                float(props["centroid_lat"]),
                {
                    "pop": int(pop),
                    "flats": flats,
                    "building_area": float(props["area_m2"]),
                    "housenumber_count": int(props["housenumber_count"]),
                    "building": props.get("building", ""),
                },
            )
        )
    write_json(POPULATION_FILE, feature_collection(features))
    return features


def matches(tags: dict, spec: dict) -> bool:
    for key, values in spec.items():
        if key not in tags:
            continue
        if values == "*" or tags[key] in values:
            return True
    return False


def classify(tags: dict) -> str | None:
    for name in PRIORITY:
        if name == "other":
            continue
        if matches(tags, CATEGORIES[name]["match"]):
            return name
    if any(key in tags for key in FUNCTION_TAGS):
        return "other"
    return None


def extract_function_categories(raw: dict) -> list[dict]:
    nodes = {
        el["id"]: (el["lon"], el["lat"])
        for el in raw["elements"]
        if el["type"] == "node" and "lon" in el and "lat" in el
    }
    points = []
    seen = set()
    for element in raw["elements"]:
        tags = element.get("tags", {})
        category = classify(tags)
        if not category:
            continue
        lon_lat = None
        area = 0.0
        if element["type"] == "node" and "lon" in element and "lat" in element:
            lon_lat = (element["lon"], element["lat"])
        elif element["type"] == "way":
            coords = way_coords(element, nodes)
            if len(coords) >= 3:
                lon_lat = centroid(coords)
                area = polygon_area_m2(coords)
        if lon_lat is None:
            continue
        key = (round(lon_lat[0], 7), round(lon_lat[1], 7), category, element.get("id"))
        if key in seen:
            continue
        seen.add(key)
        display_name = tags.get("name:zh-Hans") or tags.get("name") or ""
        points.append(
            {
                "lon": lon_lat[0],
                "lat": lon_lat[1],
                "category": category,
                "label": CATEGORIES[category]["label"],
                "area_m2": area,
                "osm_type": element["type"],
                "osm_id": element.get("id"),
                "name": display_name,
                "name_osm": tags.get("name", ""),
                "name_zh_hans": tags.get("name:zh-Hans", ""),
                "building": tags.get("building", ""),
                "amenity": tags.get("amenity", ""),
                "landuse": tags.get("landuse", ""),
                "shop": tags.get("shop", ""),
                "office": tags.get("office", ""),
                "tourism": tags.get("tourism", ""),
                "leisure": tags.get("leisure", ""),
                "aeroway": tags.get("aeroway", ""),
                "other_tags": ",".join(sorted(key for key in tags if key in FUNCTION_TAGS)),
            }
        )
    features = []
    for point in points:
        props = dict(point)
        lon = props.pop("lon")
        lat = props.pop("lat")
        features.append(point_feature(lon, lat, props))
    write_json(FUNCTION_FILE, feature_collection(features))
    return features


def estimate_jobs(category: str, area_m2: float) -> float:
    spec = WORK_CATEGORIES[category]
    floor_area = max(area_m2, 0.0) * spec["default_levels"]
    if floor_area <= 0:
        return spec["base_jobs"]
    return max(spec["base_jobs"], floor_area / spec["sqm_per_job"])


def build_work_destinations(function_features: list[dict]) -> list[dict]:
    output = []
    for idx, feature in enumerate(function_features, start=1):
        props = feature["properties"]
        category = props.get("category")
        if category not in WORK_CATEGORIES:
            continue
        lon, lat = feature["geometry"]["coordinates"]
        area_m2 = float(props.get("area_m2", 0.0) or 0.0)
        output.append(
            point_feature(
                float(lon),
                float(lat),
                {
                    "destination_id": f"D_{idx:05d}",
                    "category": category,
                    "label": CATEGORIES[category]["label"],
                    "name": props.get("name", ""),
                    "area_m2": area_m2,
                    "jobs": estimate_jobs(category, area_m2),
                    "osm_id": props.get("osm_id"),
                },
            )
        )
    write_json(WORK_DESTINATIONS_FILE, feature_collection(output))
    return output


def is_large_commercial_center(name: str) -> bool:
    clean_name = name.strip()
    return any(clean_name.endswith(suffix) for suffix in COMMERCIAL_CENTER_SUFFIXES)


def attraction_weight(category: str, area_m2: float) -> float:
    if category == "retail_service":
        return 20.0 + min(max(area_m2, 0.0) / 80.0, 500.0)
    if category == "hotel_tourism":
        return 35.0 + min(max(area_m2, 0.0) / 100.0, 650.0)
    if category == "leisure_park":
        return 45.0 + min(max(area_m2, 0.0) / 1_000.0, 800.0)
    return 10.0


def build_activity_attractions(function_features: list[dict]) -> list[dict]:
    output = []
    for idx, feature in enumerate(function_features, start=1):
        props = feature["properties"]
        category = props.get("category", "")
        if category not in ACTIVITY_COMMERCIAL_CATEGORIES and category not in ACTIVITY_TOURISM_CATEGORIES:
            continue

        name = str(props.get("name", ""))
        if category in ACTIVITY_COMMERCIAL_CATEGORIES and not is_large_commercial_center(name):
            continue

        lon, lat = feature["geometry"]["coordinates"]
        area_m2 = float(props.get("area_m2", 0.0) or 0.0)
        kind = "commercial" if category in ACTIVITY_COMMERCIAL_CATEGORIES else "tourism"
        output.append(
            point_feature(
                float(lon),
                float(lat),
                {
                    "attraction_id": f"A_{idx:05d}",
                    "kind": kind,
                    "category": category,
                    "label": CATEGORIES[category]["label"],
                    "name": name,
                    "area_m2": area_m2,
                    "weight": attraction_weight(category, area_m2),
                    "osm_id": props.get("osm_id"),
                },
            )
        )
    write_json(ACTIVITY_ATTRACTIONS_FILE, feature_collection(output))
    return output


def main() -> None:
    raw = fetch_overpass()
    buildings, exclusions, housenumbers = extract_inputs(raw)
    building_flats = estimate_building_flats(buildings, exclusions, housenumbers)
    population = distribute_population(building_flats)
    function_features = extract_function_categories(raw)
    work_destinations = build_work_destinations(function_features)
    activity_attractions = build_activity_attractions(function_features)

    print("Demand data preparation complete")
    print(f"  bbox: {BBOX}")
    print(f"  raw elements: {len(raw.get('elements', []))}")
    print(f"  buildings: {len(buildings)}")
    print(f"  buildings_with_flats: {len(building_flats)}")
    print(f"  population_points: {len(population)}")
    print(f"  total_pop: {sum(f['properties']['pop'] for f in population)}")
    print(f"  function_categories: {len(function_features)}")
    print(f"  work_destinations: {len(work_destinations)}")
    print(f"  activity_attractions: {len(activity_attractions)}")
    print(f"    commercial: {sum(1 for f in activity_attractions if f['properties']['kind'] == 'commercial')}")
    print(f"    tourism: {sum(1 for f in activity_attractions if f['properties']['kind'] == 'tourism')}")
    print(f"  output: {DEMAND_DIR}")
