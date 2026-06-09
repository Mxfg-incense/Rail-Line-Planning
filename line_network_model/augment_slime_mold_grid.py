#!/usr/bin/env python3
"""Add a small orientation-aware post-process to the slime-mold baseline."""

from __future__ import annotations

import csv
from pathlib import Path

from line_network_model.line_model_config import OUTPUT_DIR
from line_network_model.run_slime_mold_baseline import (
    CandidateEdge,
    ExtractionParams,
    build_candidate_edges,
    iterate_slime_mold,
    network_metrics,
    pair_orientation,
    render_png,
    selected_edge_set,
    station_value,
)
from line_network_model.station_selection import load_roads, load_station_file


PNG_FILE = OUTPUT_DIR / "12_slime_mold_grid_augmented.png"
EDGES_FILE = OUTPUT_DIR / "12_slime_mold_grid_augmented_edges.csv"
SUMMARY_FILE = OUTPUT_DIR / "12_slime_mold_grid_augmented_summary.csv"

BASE_PARAMS = ExtractionParams(
    keep_edge_quantile=0.58,
    max_selected_edges=28,
    min_selected_stations=34,
    max_node_degree=2,
    leaf_prune_rounds=0,
    protected_top_stations=22,
    weak_leaf_conductance_quantile=0.30,
)
MAX_AUGMENTED_EDGES = 30
MAX_AUGMENTED_BRANCH_NODES = 3
MAX_AUGMENTED_EDGE_KM = 4.25
TARGET_VERTICAL_EDGES = 7


def edge_key(edge: CandidateEdge) -> tuple[int, int]:
    return min(edge.a, edge.b), max(edge.a, edge.b)


def augmented_score(metrics: dict) -> float:
    return (
        float(metrics["selected_stations"]) * 6.0
        + float(metrics["vertical_edges"]) * 35.0
        - float(metrics["branch_nodes"]) * 18.0
        - float(metrics["leaf_nodes"]) * 5.0
        - float(metrics["total_length_km"]) * 0.8
        - max(0.0, float(metrics["max_turn_deg"]) - 165.0) * 0.8
    )


def augment_vertical_corridors(
    base_edges: list[CandidateEdge],
    reinforced_edges: list[CandidateEdge],
    stations: list[dict],
) -> list[CandidateEdge]:
    current = list(base_edges)
    while len(current) < MAX_AUGMENTED_EDGES:
        metrics = network_metrics(current, stations)
        if int(metrics["vertical_edges"]) >= TARGET_VERTICAL_EDGES:
            break

        current_pairs = {edge_key(edge) for edge in current}
        current_nodes = {node for edge in current for node in (edge.a, edge.b)}
        current_score = augmented_score(metrics)
        best: tuple[float, CandidateEdge] | None = None

        for edge in reinforced_edges:
            if edge_key(edge) in current_pairs:
                continue
            if pair_orientation(stations[edge.a], stations[edge.b]) != "vertical":
                continue
            if edge.length_m / 1000.0 > MAX_AUGMENTED_EDGE_KM:
                continue
            if edge.a not in current_nodes and edge.b not in current_nodes:
                continue

            trial = current + [edge]
            trial_metrics = network_metrics(trial, stations)
            if int(trial_metrics["components"]) != 1:
                continue
            if int(trial_metrics["branch_nodes"]) > MAX_AUGMENTED_BRANCH_NODES:
                continue
            if float(trial_metrics["max_edge_km"]) > MAX_AUGMENTED_EDGE_KM:
                continue

            new_value = sum(station_value(stations[idx]) for idx in (edge.a, edge.b) if idx not in current_nodes)
            gain = augmented_score(trial_metrics) - current_score + new_value / 900_000.0
            if best is None or gain > best[0]:
                best = (gain, edge)

        if best is None or best[0] <= 0.0:
            break
        current.append(best[1])
    return current


def write_edges_csv(edges: list[CandidateEdge], stations: list[dict]) -> None:
    with EDGES_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["from_station", "to_station", "length_km", "orientation", "conductance", "flow"],
        )
        writer.writeheader()
        for edge in sorted(edges, key=lambda item: item.conductance, reverse=True):
            writer.writerow(
                {
                    "from_station": stations[edge.a]["station_id"],
                    "to_station": stations[edge.b]["station_id"],
                    "length_km": f"{edge.length_m / 1000.0:.3f}",
                    "orientation": pair_orientation(stations[edge.a], stations[edge.b]),
                    "conductance": f"{edge.conductance:.9f}",
                    "flow": f"{edge.flow:.9f}",
                }
            )


def write_summary_csv(edges: list[CandidateEdge], stations: list[dict], base_edges: list[CandidateEdge]) -> None:
    metrics = network_metrics(edges, stations)
    base_metrics = network_metrics(base_edges, stations)
    metrics.update(
        {
            "base_selected_stations": base_metrics["selected_stations"],
            "base_selected_edges": base_metrics["selected_edges"],
            "base_vertical_edges": base_metrics["vertical_edges"],
            "base_branch_nodes": base_metrics["branch_nodes"],
            "added_edges": len(edges) - len(base_edges),
            "max_augmented_edges": MAX_AUGMENTED_EDGES,
            "target_vertical_edges": TARGET_VERTICAL_EDGES,
            "max_augmented_branch_nodes": MAX_AUGMENTED_BRANCH_NODES,
            "max_augmented_edge_km": MAX_AUGMENTED_EDGE_KM,
        }
    )
    with SUMMARY_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(metrics.keys()))
        writer.writeheader()
        writer.writerow(metrics)


def main() -> None:
    stations = load_station_file()
    roads = load_roads()
    candidate_edges = build_candidate_edges(stations)
    reinforced = iterate_slime_mold(stations, candidate_edges)
    base_edges = selected_edge_set(reinforced, stations, BASE_PARAMS)
    augmented_edges = augment_vertical_corridors(base_edges, reinforced, stations)

    render_png(augmented_edges, stations, roads, PNG_FILE, title_prefix="Slime mold grid augmented")
    write_edges_csv(augmented_edges, stations)
    write_summary_csv(augmented_edges, stations, base_edges)

    metrics = network_metrics(augmented_edges, stations)
    print("Slime mold grid augmentation complete")
    print(f"  selected_stations: {metrics['selected_stations']}")
    print(f"  selected_edges: {metrics['selected_edges']}")
    print(f"  total_length_km: {metrics['total_length_km']:.2f}")
    print(f"  branch_nodes: {metrics['branch_nodes']}")
    print(f"  vertical_edges: {metrics['vertical_edges']}")
    print(f"  horizontal_edges: {metrics['horizontal_edges']}")
    print(f"  png: {PNG_FILE}")
    print(f"  summary_csv: {SUMMARY_FILE}")


if __name__ == "__main__":
    main()
