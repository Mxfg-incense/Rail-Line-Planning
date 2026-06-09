#!/usr/bin/env python3
"""Scan slime-mold extraction parameters for more metro-like outputs."""

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
    render_png,
    selected_edge_set,
    write_edges_csv,
    write_summary_csv,
)
from line_network_model.station_selection import load_roads, load_station_file


SCAN_FILE = OUTPUT_DIR / "11_slime_mold_parameter_scan.csv"
VARIANT_DIR = OUTPUT_DIR / "slime_mold_variants"
VARIANT_DIR.mkdir(parents=True, exist_ok=True)

PARAM_GRID = [
    ExtractionParams(
        keep_edge_quantile=q,
        max_selected_edges=e,
        min_selected_stations=s,
        max_node_degree=d,
        leaf_prune_rounds=p,
        protected_top_stations=t,
        weak_leaf_conductance_quantile=w,
    )
    for q in (0.58, 0.63, 0.68, 0.73, 0.78)
    for e in (28, 34, 40, 46)
    for s in (28, 34, 40)
    for d in (2, 3)
    for p in (0, 1, 2)
    for t in (14, 18, 22)
    for w in (0.30, 0.45, 0.60)
]


def metro_like_score(metrics: dict) -> float:
    if int(metrics["components"]) != 1:
        return -1e12
    selected = float(metrics["selected_stations"])
    branches = float(metrics["branch_nodes"])
    leaves = float(metrics["leaf_nodes"])
    length = float(metrics["total_length_km"])
    max_edge = float(metrics["max_edge_km"])
    covered_value = float(metrics["covered_value"])
    return (
        selected * 8.0
        + covered_value / 120_000.0
        - branches * 26.0
        - leaves * 14.0
        - max(0.0, float(metrics["max_turn_deg"]) - 105.0) * 0.65
        - max(0.0, max_edge - 4.0) * 35.0
        - max(0.0, length - 115.0) * 1.0
    )


def balanced_score(metrics: dict) -> float:
    if int(metrics["components"]) != 1:
        return -1e12
    selected = float(metrics["selected_stations"])
    return (
        -abs(selected - 28.0) * 8.0
        + float(metrics["covered_value"]) / 125_000.0
        - float(metrics["branch_nodes"]) * 20.0
        - float(metrics["leaf_nodes"]) * 8.0
        - max(0.0, float(metrics["max_turn_deg"]) - 125.0) * 0.45
        - max(0.0, float(metrics["total_length_km"]) - 80.0) * 0.8
    )


def coverage_score(metrics: dict) -> float:
    if int(metrics["components"]) != 1:
        return -1e12
    return (
        float(metrics["selected_stations"]) * 12.0
        + float(metrics["covered_value"]) / 140_000.0
        - float(metrics["branch_nodes"]) * 14.0
        - float(metrics["leaf_nodes"]) * 5.0
        - max(0.0, float(metrics["total_length_km"]) - 105.0) * 1.2
    )


def grid_score(metrics: dict) -> float:
    if int(metrics["components"]) != 1:
        return -1e12
    selected = float(metrics["selected_stations"])
    vertical = float(metrics["vertical_edges"])
    horizontal = float(metrics["horizontal_edges"])
    branches = float(metrics["branch_nodes"])
    leaves = float(metrics["leaf_nodes"])
    return (
        selected * 9.0
        + vertical * 30.0
        + horizontal * 5.0
        + float(metrics["covered_value"]) / 150_000.0
        - abs(vertical - horizontal) * 6.0
        - branches * 32.0
        - leaves * 8.0
        - max(0.0, float(metrics["selected_edges"]) - 30.0) * 8.0
        - max(0.0, float(metrics["max_edge_km"]) - 4.0) * 35.0
        - max(0.0, float(metrics["total_length_km"]) - 95.0) * 1.0
    )


def params_row(params: ExtractionParams) -> dict:
    return {
        "keep_edge_quantile": params.keep_edge_quantile,
        "max_selected_edges": params.max_selected_edges,
        "min_selected_stations": params.min_selected_stations,
        "max_node_degree": params.max_node_degree,
        "leaf_prune_rounds": params.leaf_prune_rounds,
        "protected_top_stations": params.protected_top_stations,
        "weak_leaf_conductance_quantile": params.weak_leaf_conductance_quantile,
    }


def write_scan(rows: list[dict]) -> None:
    fieldnames = list(rows[0].keys()) if rows else []
    with SCAN_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_variant_outputs(
    rank: int,
    params: ExtractionParams,
    edges: list[CandidateEdge],
    stations: list[dict],
    roads: list[dict],
) -> None:
    stem = f"rank_{rank:02d}_q{params.keep_edge_quantile:.2f}_e{params.max_selected_edges}_s{params.min_selected_stations}_d{params.max_node_degree}_p{params.leaf_prune_rounds}"
    render_png(edges, stations, roads, VARIANT_DIR / f"{stem}.png")
    with (VARIANT_DIR / f"{stem}_edges.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["from_station", "to_station", "length_km", "conductance", "flow"])
        writer.writeheader()
        for edge in sorted(edges, key=lambda item: item.conductance, reverse=True):
            writer.writerow(
                {
                    "from_station": stations[edge.a]["station_id"],
                    "to_station": stations[edge.b]["station_id"],
                    "length_km": f"{edge.length_m / 1000.0:.3f}",
                    "conductance": f"{edge.conductance:.9f}",
                    "flow": f"{edge.flow:.9f}",
                }
            )


def main() -> None:
    stations = load_station_file()
    roads = load_roads()
    candidate_edges = build_candidate_edges(stations)
    reinforced = iterate_slime_mold(stations, candidate_edges)

    evaluated = []
    for params in PARAM_GRID:
        edges = selected_edge_set(reinforced, stations, params)
        metrics = network_metrics(edges, stations)
        row = {
            **params_row(params),
            **metrics,
            "metro_like_score": metro_like_score(metrics),
            "balanced_score": balanced_score(metrics),
            "coverage_score": coverage_score(metrics),
            "grid_score": grid_score(metrics),
        }
        evaluated.append((row["metro_like_score"], row, params, edges))
    evaluated.sort(key=lambda item: item[0], reverse=True)
    write_scan([row for _, row, _, _ in evaluated])

    for rank, (_, row, params, edges) in enumerate(evaluated[:6], start=1):
        write_variant_outputs(rank, params, edges, stations, roads)

    profile_choices = {
        "sparse": max(evaluated, key=lambda item: metro_like_score(item[1])),
        "balanced": max(evaluated, key=lambda item: balanced_score(item[1])),
        "coverage": max(evaluated, key=lambda item: coverage_score(item[1])),
        "grid": max(evaluated, key=lambda item: grid_score(item[1])),
    }
    for name, (_, row, params, edges) in profile_choices.items():
        render_png(edges, stations, roads, OUTPUT_DIR / f"10_slime_mold_{name}.png")
        with (OUTPUT_DIR / f"10_slime_mold_{name}_summary.csv").open("w", encoding="utf-8-sig", newline="") as f:
            out = {**params_row(params), **row}
            writer = csv.DictWriter(f, fieldnames=list(out.keys()))
            writer.writeheader()
            writer.writerow(out)

    best_score, best_row, best_params, best_edges = profile_choices["coverage"]
    render_png(best_edges, stations, roads, OUTPUT_DIR / "10_slime_mold_network.png")
    write_edges_csv(best_edges, stations)
    write_summary_csv(best_edges, stations, len(candidate_edges), best_params)

    print("Slime mold parameter scan complete")
    print(f"  variants_evaluated: {len(evaluated)}")
    print(f"  default_profile: coverage")
    print(f"  default_score: {best_score:.3f}")
    print(f"  best_params: {params_row(best_params)}")
    print(
        "  best_metrics: "
        f"stations={best_row['selected_stations']}, edges={best_row['selected_edges']}, "
        f"branches={best_row['branch_nodes']}, length_km={float(best_row['total_length_km']):.2f}"
    )
    print(f"  scan_csv: {SCAN_FILE}")
    print(f"  variant_png_dir: {VARIANT_DIR}")
    for name, (_, row, params, _) in profile_choices.items():
        print(
            f"  {name}: stations={row['selected_stations']}, edges={row['selected_edges']}, "
            f"branches={row['branch_nodes']}, length_km={float(row['total_length_km']):.2f}, "
            f"params={params_row(params)}"
        )


if __name__ == "__main__":
    main()
