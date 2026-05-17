#!/usr/bin/env python3
"""Step 01: select candidate stations from population clusters and rail hubs."""

from __future__ import annotations

from station_selection import (
    MIN_STATION_DISTANCE_M,
    build_layer_centers,
    demand_bounds,
    load_activity_attractions,
    load_commercial_hubs,
    load_jobs,
    load_population,
    load_roads,
    load_transport_hubs,
    minimum_station_distance,
    select_candidate_stations_from_centers,
    write_layer_center_geojsons,
    write_layer_center_svgs,
    write_station_geojson,
    write_station_selection_svg,
    write_station_table,
)


def main() -> None:
    population = load_population()
    jobs = load_jobs()
    attractions = load_activity_attractions(demand_bounds(population))
    transport_hubs = load_transport_hubs()
    commercial_hubs = load_commercial_hubs(attractions)
    roads = load_roads()
    layer_centers = build_layer_centers(population, jobs, transport_hubs, commercial_hubs)
    stations = select_candidate_stations_from_centers(layer_centers)

    write_layer_center_geojsons(layer_centers)
    write_layer_center_svgs(layer_centers, population, jobs, roads)
    write_station_geojson(stations)
    write_station_table(stations)
    write_station_selection_svg(population, jobs, attractions, stations, roads)

    n_hubs = sum(1 for station in stations if station["required"])
    n_airport_hubs = sum(1 for station in stations if "airport_hub" in station.get("sources", ""))
    n_rail_hubs = sum(1 for station in stations if "rail_hub" in station.get("sources", ""))
    n_commercial_hubs = sum(1 for station in stations if station.get("commercial_value", 0.0) > 0)
    total_covered_pop = sum(station["covered_pop"] for station in stations)
    total_covered_jobs = sum(station["covered_jobs"] for station in stations)
    total_covered_commercial = sum(station["covered_commercial"] for station in stations)
    total_covered_tourism = sum(station["covered_tourism"] for station in stations)
    total_value = sum(station["total_value"] for station in stations)
    commercial_count = sum(1 for point in attractions if point["kind"] == "commercial")
    tourism_count = sum(1 for point in attractions if point["kind"] == "tourism")
    min_dist = minimum_station_distance(stations)
    print("Station selection complete")
    print(f"  population points: {len(population)}")
    print(f"  job destinations: {len(jobs)}")
    print(f"  commercial attractions: {commercial_count}")
    print(f"  tourism attractions: {tourism_count}")
    print(f"  raw transport hub candidates: {len(transport_hubs)}")
    print(f"  raw commercial hub candidates: {len(commercial_hubs)}")
    print(f"  population centers: {len(layer_centers['population'])}")
    print(f"  job centers: {len(layer_centers['job'])}")
    print(f"  transport fixed centers: {len(layer_centers['transport'])}")
    print(f"  commercial fixed centers: {len(layer_centers['commercial'])}")
    print(f"  road ways: {len(roads)}")
    print(f"  selected stations: {len(stations)}")
    print(f"  selected required hubs: {n_hubs}")
    print(f"  selected rail hubs: {n_rail_hubs}")
    print(f"  selected airport hubs: {n_airport_hubs}")
    print(f"  selected commercial hubs: {n_commercial_hubs}")
    print(f"  summed station covered_pop: {total_covered_pop:.1f}")
    print(f"  summed station covered_jobs: {total_covered_jobs:.1f}")
    print(f"  summed station covered_commercial: {total_covered_commercial:.1f}")
    print(f"  summed station covered_tourism: {total_covered_tourism:.1f}")
    print(f"  summed station total_value: {total_value:.1f}")
    print(f"  min station distance: {min_dist:.1f} m")
    print(f"  spacing rule: > {MIN_STATION_DISTANCE_M:.0f} m")


if __name__ == "__main__":
    main()
