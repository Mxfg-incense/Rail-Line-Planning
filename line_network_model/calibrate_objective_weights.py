#!/usr/bin/env python3
"""Calibrate linear reward objective weights against the manual route."""

from __future__ import annotations

import csv
import itertools

from line_network_model.line_model_config import OUTPUT_DIR
from line_network_model.manual_route import manual_solution, spacing_violations_for_lines
from line_network_model.objective_config import (
    CONSTRUCTION_COST_WEIGHT_PER_M,
    TURN_PENALTY_WEIGHT,
)
from line_network_model.run_baseline import (
    baseline_greedy_value_tree,
    solution_components,
    solution_metrics,
)
from line_network_model.station_selection import STATION_TABLE_FILE, load_station_file


CANDIDATE_FILE = OUTPUT_DIR / "07_objective_calibration_candidates.csv"
RECOMMENDATION_FILE = OUTPUT_DIR / "08_objective_calibration_recommendation.csv"

CUTOFF_GRID_M = [8_000, 12_000, 16_000, 20_000, 25_000, 30_000, 40_000, 50_000]
CONSTRUCTION_WEIGHT_GRID = [0, 1_000, 3_000, 10_000, 20_000, 50_000, 100_000]
TURN_WEIGHT_GRID = [0, 10_000_000, 50_000_000, 100_000_000, 170_000_000, 300_000_000, 500_000_000]


def route_label(solution: dict, stations: list[dict]) -> str:
    lines = solution.get("lines")
    line_ids = solution.get("line_ids", [])
    if lines:
        return " | ".join(
            f"{line_ids[pos] if pos < len(line_ids) else f'L{pos + 1}'}:"
            + ",".join(stations[idx]["station_id"] for idx in line)
            for pos, line in enumerate(lines)
        )
    if "route" in solution:
        return ",".join(stations[idx]["station_id"] for idx in solution["route"])
    return ",".join(stations[idx]["station_id"] for idx in sorted(solution["selected"]))


def component_row(
    solution: dict,
    stations: list[dict],
    cutoff_m: float,
    construction_weight_per_m: float,
    turn_penalty_weight: float,
) -> dict:
    base = raw_component_row(solution, stations, cutoff_m)
    return weighted_component_row(base, construction_weight_per_m, turn_penalty_weight)


def raw_component_row(solution: dict, stations: list[dict], cutoff_m: float) -> dict:
    components = solution_components(
        solution,
        stations,
        cutoff_m=cutoff_m,
        construction_weight_per_m=0.0,
        turn_penalty_weight=0.0,
    )
    metrics = solution_metrics(solution, stations)
    return {
        "method": solution["method"],
        "station_ids": route_label(solution, stations),
        "selected_stations": len(solution["selected"]),
        "selected_edges": len(solution["edges"]),
        "cutoff_m": cutoff_m,
        "length_km": components["length_m"] / 1000.0,
        "pair_reward": components["pair_reward"],
        "length_m": components["length_m"],
        "construction_weight_per_m": 0.0,
        "construction_penalty": 0.0,
        "turn_penalty_weight": 0.0,
        "turn_excess_squared": components["turn_excess_squared"],
        "turn_penalty": 0.0,
        "endpoint_count": components["endpoint_count"],
        "endpoint_penalty": components["endpoint_penalty"],
        "objective_score": components["pair_reward"],
        "max_turn_deg": metrics["max_turn_deg"],
        "penalized_turns": metrics["penalized_turns"],
    }


def weighted_component_row(base: dict, construction_weight_per_m: float, turn_penalty_weight: float) -> dict:
    row = dict(base)
    construction_penalty = construction_weight_per_m * float(row["length_m"])
    turn_penalty = turn_penalty_weight * float(row["turn_excess_squared"])
    row["construction_weight_per_m"] = construction_weight_per_m
    row["construction_penalty"] = construction_penalty
    row["turn_penalty_weight"] = turn_penalty_weight
    row["turn_penalty"] = turn_penalty
    row["objective_score"] = float(row["pair_reward"]) - construction_penalty - turn_penalty
    return row


def find_best_parameters(manual: dict, greedy: dict, stations: list[dict]) -> dict:
    best = None
    for cutoff_m in CUTOFF_GRID_M:
        manual_base = raw_component_row(manual, stations, cutoff_m)
        greedy_base = raw_component_row(greedy, stations, cutoff_m)
        for construction_weight, turn_weight in itertools.product(
            CONSTRUCTION_WEIGHT_GRID,
            TURN_WEIGHT_GRID,
        ):
            manual_row = weighted_component_row(manual_base, construction_weight, turn_weight)
            greedy_row = weighted_component_row(greedy_base, construction_weight, turn_weight)
            margin = manual_row["objective_score"] - greedy_row["objective_score"]
            key = (margin, manual_row["objective_score"], -construction_weight, -turn_weight)
            if best is None or key > best["key"]:
                best = {
                    "cutoff_m": cutoff_m,
                    "construction_weight_per_m": construction_weight,
                    "turn_penalty_weight": turn_weight,
                    "manual_row": manual_row,
                    "greedy_row": greedy_row,
                    "margin": margin,
                    "key": key,
                }
    return best


def write_candidates(rows: list[dict]) -> None:
    fieldnames = [
        "method",
        "station_ids",
        "selected_stations",
        "selected_edges",
        "cutoff_m",
        "length_km",
        "pair_reward",
        "length_m",
        "construction_weight_per_m",
        "construction_penalty",
        "turn_penalty_weight",
        "turn_excess_squared",
        "turn_penalty",
        "endpoint_count",
        "endpoint_penalty",
        "objective_score",
        "max_turn_deg",
        "penalized_turns",
    ]
    with CANDIDATE_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            out = dict(row)
            for key in (
                "length_km",
                "pair_reward",
                "length_m",
                "construction_penalty",
                "turn_excess_squared",
                "turn_penalty",
                "endpoint_penalty",
                "objective_score",
                "max_turn_deg",
            ):
                out[key] = f"{float(row[key]):.6f}"
            writer.writerow(out)


def write_recommendation(best: dict, manual: dict, stations: list[dict]) -> None:
    manual_row = best["manual_row"]
    greedy_row = best["greedy_row"]
    violations = spacing_violations_for_lines(manual["lines"], stations)
    fieldnames = [
        "cutoff_m",
        "construction_weight_per_m",
        "turn_penalty_weight",
        "manual_score",
        "greedy_score",
        "manual_minus_greedy",
        "manual_pair_reward",
        "greedy_pair_reward",
        "manual_length_km",
        "greedy_length_km",
        "manual_turn_excess_squared",
        "greedy_turn_excess_squared",
        "manual_endpoint_count",
        "greedy_endpoint_count",
        "objective",
        "spacing_violation_count",
        "spacing_violations",
    ]
    with RECOMMENDATION_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "cutoff_m": best["cutoff_m"],
                "construction_weight_per_m": best["construction_weight_per_m"],
                "turn_penalty_weight": best["turn_penalty_weight"],
                "manual_score": f'{manual_row["objective_score"]:.6f}',
                "greedy_score": f'{greedy_row["objective_score"]:.6f}',
                "manual_minus_greedy": f'{best["margin"]:.6f}',
                "manual_pair_reward": f'{manual_row["pair_reward"]:.6f}',
                "greedy_pair_reward": f'{greedy_row["pair_reward"]:.6f}',
                "manual_length_km": f'{manual_row["length_km"]:.6f}',
                "greedy_length_km": f'{greedy_row["length_km"]:.6f}',
                "manual_turn_excess_squared": f'{manual_row["turn_excess_squared"]:.9f}',
                "greedy_turn_excess_squared": f'{greedy_row["turn_excess_squared"]:.9f}',
                "manual_endpoint_count": manual_row["endpoint_count"],
                "greedy_endpoint_count": greedy_row["endpoint_count"],
                "objective": (
                    "sum v_i*v_j*max(0,1-D_ij/cutoff_m) "
                    "- construction_weight_per_m*length_m "
                    "- turn_penalty_weight*turn_excess_squared"
                ),
                "spacing_violation_count": len(violations),
                "spacing_violations": "; ".join(
                    f'{item["from_station"]}-{item["to_station"]}:{item["length_m"]:.1f}m'
                    for item in violations
                ),
            }
        )


def main() -> None:
    if not STATION_TABLE_FILE.exists():
        raise FileNotFoundError(
            f"{STATION_TABLE_FILE} not found. Run: uv run python -m line_network_model.01_select_stations"
        )

    stations = load_station_file()
    manual = manual_solution(stations)
    greedy = baseline_greedy_value_tree(stations)
    best = find_best_parameters(manual, greedy, stations)
    write_candidates([best["manual_row"], best["greedy_row"]])
    write_recommendation(best, manual, stations)

    print("Objective calibration complete")
    print(f"  cutoff_m: {best['cutoff_m']}")
    print(f"  construction_weight_per_m: {best['construction_weight_per_m']}")
    print(f"  turn_penalty_weight: {best['turn_penalty_weight']}")
    print(f"  manual_minus_greedy: {best['margin']:.3f}")
    print(f"  candidates_csv: {CANDIDATE_FILE}")
    print(f"  recommendation_csv: {RECOMMENDATION_FILE}")


if __name__ == "__main__":
    main()
