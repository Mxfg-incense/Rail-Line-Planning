"""Helpers for loading manually drawn intuition routes."""

from __future__ import annotations

import csv
from pathlib import Path

from line_network_model.line_model_config import OUTPUT_DIR
from line_network_model.objective_config import MIN_EDGE_LENGTH_M
from line_network_model.station_selection import distance_m


HERE = Path(__file__).resolve().parent
MANUAL_ROUTE_FILE = HERE / "manual_intuition_route.csv"
MANUAL_ROUTE_SVG_FILE = OUTPUT_DIR / "04_manual_intuition_route.svg"


def station_index_by_id(stations: list[dict]) -> dict[str, int]:
    return {station["station_id"]: idx for idx, station in enumerate(stations)}


def load_manual_lines(path: Path = MANUAL_ROUTE_FILE) -> list[tuple[str, list[str]]]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Create it with columns: line_id,order,station_id,note"
        )

    rows_by_line: dict[str, list[tuple[int, str]]] = {}
    seen_line_orders = set()
    duplicate_line_orders = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required_fields = {"line_id", "order", "station_id"}
        if reader.fieldnames is None or not required_fields.issubset(set(reader.fieldnames)):
            raise ValueError(f"{path} must have columns: line_id,order,station_id,note")
        for line_no, row in enumerate(reader, start=2):
            line_id = str(row.get("line_id", "")).strip()
            station_id = str(row.get("station_id", "")).strip()
            if not line_id and not station_id:
                continue
            if not line_id or not station_id:
                raise ValueError(f"Line {line_no} must include both line_id and station_id")
            try:
                order = int(str(row.get("order", "")).strip())
            except ValueError as exc:
                raise ValueError(f"Invalid order on line {line_no}: {row.get('order')}") from exc
            key = (line_id, order)
            if key in seen_line_orders:
                duplicate_line_orders.append(f"{line_id}:{order}")
            seen_line_orders.add(key)
            rows_by_line.setdefault(line_id, []).append((order, station_id))

    if duplicate_line_orders:
        raise ValueError("Duplicate manual line orders: " + ", ".join(duplicate_line_orders))
    if not rows_by_line:
        raise ValueError("Manual route file must contain at least one line")

    lines = []
    short_lines = []
    for line_id, rows in rows_by_line.items():
        rows.sort(key=lambda item: item[0])
        station_ids = [station_id for _, station_id in rows]
        if len(station_ids) < 2:
            short_lines.append(line_id)
        if len(station_ids) != len(set(station_ids)):
            raise ValueError(f"Manual line {line_id} has duplicate station_ids")
        lines.append((line_id, station_ids))

    if short_lines:
        raise ValueError("Each manual line must contain at least two stations: " + ", ".join(short_lines))
    lines.sort(key=lambda item: item[0])
    return lines


def load_manual_route_ids(path: Path = MANUAL_ROUTE_FILE) -> list[str]:
    station_ids = []
    for _line_id, line_station_ids in load_manual_lines(path):
        station_ids.extend(line_station_ids)
    return station_ids


def manual_line_indices(stations: list[dict], path: Path = MANUAL_ROUTE_FILE) -> list[tuple[str, list[int]]]:
    line_ids = load_manual_lines(path)
    by_id = station_index_by_id(stations)
    missing = [
        station_id
        for _line_id, route_ids in line_ids
        for station_id in route_ids
        if station_id not in by_id
    ]
    if missing:
        raise ValueError("Manual route has unknown station_ids: " + ", ".join(missing))
    return [(line_id, [by_id[station_id] for station_id in route_ids]) for line_id, route_ids in line_ids]


def manual_route_indices(stations: list[dict], path: Path = MANUAL_ROUTE_FILE) -> list[int]:
    route = []
    for _line_id, line in manual_line_indices(stations, path):
        route.extend(line)
    return route


def manual_solution(stations: list[dict], path: Path = MANUAL_ROUTE_FILE) -> dict:
    named_lines = manual_line_indices(stations, path)
    lines = [line for _line_id, line in named_lines]
    edges = []
    for line in lines:
        edges.extend(zip(line, line[1:]))
    selected = {idx for line in lines for idx in line}
    return {
        "method": "manual_intuition_route",
        "selected": selected,
        "edges": list(dict.fromkeys(tuple(sorted(edge)) for edge in edges)),
        "lines": lines,
        "line_ids": [line_id for line_id, _line in named_lines],
    }


def spacing_violations_for_lines(lines: list[list[int]], stations: list[dict]) -> list[dict]:
    violations = []
    for line in lines:
        for a, b in zip(line, line[1:]):
            length_m = distance_m(
                (stations[a]["lon"], stations[a]["lat"]),
                (stations[b]["lon"], stations[b]["lat"]),
            )
            if length_m < MIN_EDGE_LENGTH_M:
                violations.append(
                    {
                        "from_station": stations[a]["station_id"],
                        "to_station": stations[b]["station_id"],
                        "length_m": length_m,
                        "min_edge_length_m": MIN_EDGE_LENGTH_M,
                    }
                )
    return violations


def spacing_violations(route: list[int], stations: list[dict]) -> list[dict]:
    return spacing_violations_for_lines([route], stations)
