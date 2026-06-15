"""Systematic evaluation of all reproduced methods.

Runs evaluation metrics (coverage, efficiency, transfer, robustness) on:
  - Laporte 2005: H1, H2-A, H2-C
  - Ahmed 2020: 1-line, 2-line, 3-line GA
  - dev-OD baselines: greedy_od, greedy_bcr, ga, ilp (if available)

Outputs:
  - output/evaluation/all_methods_comparison.csv
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import numpy as np

from line_network_model.ahmed2020 import (
    AhmedInstance, _select_terminal_pairs, select_lines_ahmed_ga,
    DEFAULT_GA_PARAMS,
)
from line_network_model.corridor import build_corridor_mst_plus
from line_network_model.data import DEFAULT_STATION_CSV, load_stations
from line_network_model.evaluation import evaluate_network
from line_network_model.laporte2005 import (
    MTCPInstance, h1_greedy_extension, h2_greedy_insertion,
)
from line_network_model.od import generate_od_matrix
from line_network_model.selection import (
    select_lines_greedy_od, select_lines_greedy_bcr, select_lines_ga,
)

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "evaluation"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def laporte_result_to_lines(result: dict, corridor) -> list[dict]:
    """Convert Laporte result dict to evaluate_network format."""
    path = result["path"]
    length = result["length_m"]
    cost = sum(
        corridor[u][v].get("construction_cost", 0.0)
        for u, v in zip(path[:-1], path[1:])
        if corridor.has_edge(u, v)
    )
    return [{
        "station_sequence": path,
        "length": length,
        "construction_cost": cost,
        "covered_population": 0.0,
        "direct_od_coverage": 0.0,
    }]


def ahmed_result_to_lines(result: dict, corridor) -> list[dict]:
    """Convert Ahmed result dict to evaluate_network format."""
    chromosome = result.get("chromosome") or []
    lines = []
    for line_seq in chromosome:
        length = sum(
            corridor[u][v].get("distance", 0.0)
            for u, v in zip(line_seq[:-1], line_seq[1:])
            if corridor.has_edge(u, v)
        )
        cost = sum(
            corridor[u][v].get("construction_cost", 0.0)
            for u, v in zip(line_seq[:-1], line_seq[1:])
            if corridor.has_edge(u, v)
        )
        lines.append({
            "station_sequence": line_seq,
            "length": length,
            "construction_cost": cost,
            "covered_population": 0.0,
            "direct_od_coverage": 0.0,
        })
    return lines


def devod_result_to_lines(selected: list[dict]) -> list[dict]:
    """dev-OD selection results are already in the right format."""
    return selected


def run_all_evaluations() -> pd.DataFrame:
    """Run evaluation on all methods and return a comparison table."""
    stations = load_stations(DEFAULT_STATION_CSV)
    od = generate_od_matrix(stations, alpha=1.5, normalize=True)
    corridor = build_corridor_mst_plus(stations, extra_edges_ratio=0.45)

    rows = []

    # ---- Laporte 2005 ----
    instance = MTCPInstance(
        stations=stations, od_matrix=od, corridor_graph=corridor, lmax=60000.0,
    )
    for algo_name, algo_fn in [
        ("Laporte-H1", h1_greedy_extension),
        ("Laporte-H2A", lambda inst: h2_greedy_insertion(inst, "A")),
        ("Laporte-H2C", lambda inst: h2_greedy_insertion(inst, "C")),
    ]:
        t0 = time.perf_counter()
        result = algo_fn(instance)
        elapsed = time.perf_counter() - t0
        lines = laporte_result_to_lines(result, corridor)
        metrics = evaluate_network(lines, stations, od, corridor)
        metrics["method"] = algo_name
        metrics["runtime_s"] = round(elapsed, 2)
        metrics["n_stations_selected"] = result["n_stations"]
        metrics["total_length_km"] = round(result["length_m"] / 1000.0, 1)
        rows.append(metrics)
        print(f"  {algo_name}: {metrics['n_stations_selected']} stns, "
              f"{metrics['total_length_km']}km, "
              f"pop_cov={metrics['population_coverage_ratio']:.2%}, "
              f"od_cov={metrics['direct_od_coverage_ratio']:.2%}")

    # ---- Ahmed 2020 ----
    ga_params = {**DEFAULT_GA_PARAMS, "population_size": 80, "generations": 30}
    for n_lines in [1, 2, 3]:
        terminals = _select_terminal_pairs(stations, od, n_lines)
        ahmed_inst = AhmedInstance(
            stations=stations, od_matrix=od, corridor_graph=corridor,
            terminal_pairs=terminals,
        )
        t0 = time.perf_counter()
        result = select_lines_ahmed_ga(ahmed_inst, ga_params=ga_params, verbose=False)
        elapsed = time.perf_counter() - t0
        lines = ahmed_result_to_lines(result, corridor)
        metrics = evaluate_network(lines, stations, od, corridor)
        metrics["method"] = f"Ahmed-GA-{n_lines}L"
        metrics["runtime_s"] = round(elapsed, 1)
        metrics["n_stations_selected"] = result["n_stations_selected"]
        metrics["total_length_km"] = round(result["total_length_km"], 1)
        rows.append(metrics)
        print(f"  Ahmed-GA-{n_lines}L: {metrics['n_stations_selected']} stns, "
              f"{metrics['total_length_km']}km, "
              f"pop_cov={metrics['population_coverage_ratio']:.2%}, "
              f"od_cov={metrics['direct_od_coverage_ratio']:.2%}")

    # ---- dev-OD baselines (line pool) ----
    from line_network_model.line_pool import generate_candidate_line_pool
    line_pool = generate_candidate_line_pool(stations, corridor, od)

    for bas_name, bas_fn in [
        ("Greedy-OD", select_lines_greedy_od),
        ("Greedy-BCR", select_lines_greedy_bcr),
    ]:
        t0 = time.perf_counter()
        selected = bas_fn(line_pool, od, max_lines=3)
        elapsed = time.perf_counter() - t0
        metrics = evaluate_network(selected, stations, od, corridor)
        metrics["method"] = bas_name
        metrics["runtime_s"] = round(elapsed, 2)
        metrics["n_stations_selected"] = len(
            {s for line in selected for s in line["station_sequence"]}
        )
        metrics["total_length_km"] = round(
            sum(line["length"] for line in selected), 1
        )
        rows.append(metrics)
        print(f"  {bas_name}: {metrics['n_stations_selected']} stns, "
              f"{metrics['total_length_km']}km, "
              f"pop_cov={metrics['population_coverage_ratio']:.2%}, "
              f"od_cov={metrics['direct_od_coverage_ratio']:.2%}")

    try:
        t0 = time.perf_counter()
        selected_ga = select_lines_ga(line_pool, od, max_lines=3)
        elapsed = time.perf_counter() - t0
        metrics = evaluate_network(selected_ga, stations, od, corridor)
        metrics["method"] = "GA (line-pool)"
        metrics["runtime_s"] = round(elapsed, 1)
        metrics["n_stations_selected"] = len(
            {s for line in selected_ga for s in line["station_sequence"]}
        )
        metrics["total_length_km"] = round(
            sum(line["length"] for line in selected_ga), 1
        )
        rows.append(metrics)
        print(f"  GA (line-pool): {metrics['n_stations_selected']} stns, "
              f"{metrics['total_length_km']}km")
    except Exception:
        pass

    # ---- Build comparison table ----
    cols = [
        "method", "n_stations_selected", "total_length_km", "runtime_s",
        "num_lines", "num_transfer_stations",
        "population_coverage_ratio", "direct_od_coverage_ratio",
        "network_od_coverage_ratio",
        "weighted_avg_detour_ratio", "weighted_avg_transfer_count",
        "largest_component_station_ratio", "average_node_degree",
        "network_efficiency", "num_connected_components",
        "num_stations_covered",
    ]
    df = pd.DataFrame(rows)
    available_cols = [c for c in cols if c in df.columns]
    table = df[available_cols].set_index("method")
    table.to_csv(OUTPUT_DIR / "all_methods_comparison.csv", encoding="utf-8-sig")
    return table


def main() -> None:
    print("=== Systematic Evaluation of All Methods ===\n")
    table = run_all_evaluations()

    # Print key metrics
    ratio_cols = [
        "population_coverage_ratio",
        "direct_od_coverage_ratio",
        "network_od_coverage_ratio",
        "weighted_avg_detour_ratio",
        "weighted_avg_transfer_count",
    ]
    avail = [c for c in ratio_cols if c in table.columns]
    print(f"\n=== Comparison Table ===\n")
    print(table[avail + ["n_stations_selected", "total_length_km", "runtime_s"]]
          .round(4).to_string())
    print(f"\nFull results: {OUTPUT_DIR / 'all_methods_comparison.csv'}")


if __name__ == "__main__":
    main()
