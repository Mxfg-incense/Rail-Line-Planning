#!/usr/bin/env python3
"""Genetic-algorithm baseline for the value-connection model.

Chromosome: a permutation of candidate stations. The decoder starts from the
required transport centers when that option is enabled; otherwise it starts from
the best value pair. It then attaches stations in chromosome order when doing so
improves the penalized objective. Fitness is the same formal objective used by
the other baselines:

    sum_{i<j selected} total_value_i * total_value_j
        * max(0, 1 - distance(i, j) / cutoff_m)
    - lambda_1 * construction length
    - lambda_2 * turn-angle penalty

Endpoint count is reported as a diagnostic but is not part of fitness.
"""

from __future__ import annotations

import csv
import random

from line_network_model.run_baseline import (
    COMPARISON_FILE,
    OUTPUT_DIR,
    best_value_pair,
    minimum_spanning_edges,
    nearest_selected_edge,
    objective_score,
    output_path,
    required_indices,
    route_edges,
    solution_metrics,
    write_svg,
)
from line_network_model.station_selection import STATION_TABLE_FILE, load_population, load_roads, load_station_file


RANDOM_SEED = 42
POPULATION_SIZE = 60
GENERATIONS = 100
TOURNAMENT_SIZE = 4
ELITE_COUNT = 8
CROSSOVER_RATE = 0.85
MUTATION_RATE = 0.22

METHOD = "genetic_value_tree"
SUMMARY_FILE = OUTPUT_DIR / "06_genetic_baseline_summary.csv"


def initial_edges(stations: list[dict]) -> tuple[set[int], list[tuple[int, int]]]:
    required = required_indices(stations)
    if required:
        return set(required), minimum_spanning_edges(required, stations)
    route = best_value_pair(stations)
    return set(route), route_edges(route)


def decode_chromosome(chromosome: list[int], stations: list[dict]) -> dict:
    selected, edges = initial_edges(stations)
    current_solution = {"method": METHOD, "selected": selected, "edges": edges}
    current_score = objective_score(current_solution, stations)

    for station_idx in chromosome:
        if station_idx in selected:
            continue
        connector = nearest_selected_edge(station_idx, selected, stations)
        if connector is None:
            continue
        candidate_selected = selected | {station_idx}
        candidate_edges = edges + [(connector[0], connector[1])]
        candidate_solution = {"method": METHOD, "selected": candidate_selected, "edges": candidate_edges}
        candidate_score = objective_score(candidate_solution, stations)
        if candidate_score <= current_score:
            continue
        selected = candidate_selected
        edges = candidate_edges
        current_solution = candidate_solution
        current_score = candidate_score

    return {"method": METHOD, "selected": selected, "edges": edges}


def fitness(chromosome: list[int], stations: list[dict]) -> float:
    solution = decode_chromosome(chromosome, stations)
    return objective_score(solution, stations)


def make_initial_population(stations: list[dict], rng: random.Random) -> list[list[int]]:
    initial_selected, _ = initial_edges(stations)
    genes = [idx for idx in range(len(stations)) if idx not in initial_selected]
    population = []

    value_order = sorted(genes, key=lambda idx: stations[idx].get("total_value", 0.0), reverse=True)
    population.append(value_order)

    for _ in range(POPULATION_SIZE - 1):
        chromosome = genes[:]
        rng.shuffle(chromosome)
        population.append(chromosome)
    return population


def tournament_select(
    population: list[list[int]],
    scores: list[float],
    rng: random.Random,
) -> list[int]:
    contenders = rng.sample(range(len(population)), min(TOURNAMENT_SIZE, len(population)))
    best = max(contenders, key=lambda idx: scores[idx])
    return population[best][:]


def ordered_crossover(parent_a: list[int], parent_b: list[int], rng: random.Random) -> list[int]:
    if len(parent_a) < 2 or rng.random() > CROSSOVER_RATE:
        return parent_a[:]
    left, right = sorted(rng.sample(range(len(parent_a)), 2))
    child = [None] * len(parent_a)
    child[left : right + 1] = parent_a[left : right + 1]
    used = set(child[left : right + 1])
    fill = [gene for gene in parent_b if gene not in used]
    fill_iter = iter(fill)
    for idx, gene in enumerate(child):
        if gene is None:
            child[idx] = next(fill_iter)
    return child


def mutate(chromosome: list[int], rng: random.Random) -> None:
    if len(chromosome) < 2 or rng.random() > MUTATION_RATE:
        return
    move = rng.choice(["swap", "reverse", "insert"])
    i, j = sorted(rng.sample(range(len(chromosome)), 2))
    if move == "swap":
        chromosome[i], chromosome[j] = chromosome[j], chromosome[i]
    elif move == "reverse":
        chromosome[i : j + 1] = reversed(chromosome[i : j + 1])
    else:
        gene = chromosome.pop(j)
        chromosome.insert(i, gene)


def run_ga(stations: list[dict]) -> tuple[dict, list[dict]]:
    rng = random.Random(RANDOM_SEED)
    population = make_initial_population(stations, rng)
    history = []
    best_chromosome = None
    best_score = float("-inf")

    for generation in range(GENERATIONS + 1):
        scores = [fitness(chromosome, stations) for chromosome in population]
        generation_best_idx = max(range(len(population)), key=lambda idx: scores[idx])
        generation_best = scores[generation_best_idx]
        mean_score = sum(scores) / len(scores)
        if generation_best > best_score:
            best_score = generation_best
            best_chromosome = population[generation_best_idx][:]

        if generation % 10 == 0 or generation == GENERATIONS:
            history.append(
                {
                    "generation": generation,
                    "best_fitness": generation_best,
                    "mean_fitness": mean_score,
                    "global_best": best_score,
                }
            )

        if generation == GENERATIONS:
            break

        ranked = sorted(range(len(population)), key=lambda idx: scores[idx], reverse=True)
        next_population = [population[idx][:] for idx in ranked[:ELITE_COUNT]]
        while len(next_population) < POPULATION_SIZE:
            parent_a = tournament_select(population, scores, rng)
            parent_b = tournament_select(population, scores, rng)
            child = ordered_crossover(parent_a, parent_b, rng)
            mutate(child, rng)
            next_population.append(child)
        population = next_population

    return decode_chromosome(best_chromosome, stations), history


def write_ga_summary(solution: dict, history: list[dict], stations: list[dict]) -> None:
    metrics = solution_metrics(solution, stations)
    with SUMMARY_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "type",
                "generation",
                "best_fitness",
                "mean_fitness",
                "global_best",
                "selected_stations",
                "selected_edges",
                "selected_required",
                "length_km",
                "pair_reward",
                "construction_cost",
                "turn_penalty",
                "endpoint_count",
                "endpoint_penalty",
                "max_turn_deg",
                "penalized_turns",
                "connectivity_score",
                "objective_score",
                "objective_reward",
                "reward_per_km",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "type": "final",
                "generation": GENERATIONS,
                "best_fitness": f'{metrics["objective_reward"]:.6f}',
                "mean_fitness": "",
                "global_best": f'{metrics["objective_reward"]:.6f}',
                "selected_stations": metrics["selected_stations"],
                "selected_edges": metrics["selected_edges"],
                "selected_required": metrics["selected_required"],
                "length_km": f'{metrics["length_km"]:.3f}',
                "pair_reward": f'{metrics["pair_reward"]:.6f}',
                "construction_cost": f'{metrics["construction_cost"]:.6f}',
                "turn_penalty": f'{metrics["turn_penalty"]:.6f}',
                "endpoint_count": metrics["endpoint_count"],
                "endpoint_penalty": f'{metrics["endpoint_penalty"]:.6f}',
                "max_turn_deg": f'{metrics["max_turn_deg"]:.3f}',
                "penalized_turns": metrics["penalized_turns"],
                "connectivity_score": f'{metrics["connectivity_score"]:.6f}',
                "objective_score": f'{metrics["objective_score"]:.6f}',
                "objective_reward": f'{metrics["objective_score"]:.6f}',
                "reward_per_km": f'{metrics["reward_per_km"]:.6f}',
            }
        )
        for row in history:
            writer.writerow(
                {
                    "type": "history",
                    "generation": row["generation"],
                    "best_fitness": f'{row["best_fitness"]:.6f}',
                    "mean_fitness": f'{row["mean_fitness"]:.6f}',
                    "global_best": f'{row["global_best"]:.6f}',
                    "selected_stations": "",
                    "selected_edges": "",
                    "selected_required": "",
                    "length_km": "",
                    "pair_reward": "",
                    "construction_cost": "",
                    "turn_penalty": "",
                    "endpoint_count": "",
                    "endpoint_penalty": "",
                    "max_turn_deg": "",
                    "penalized_turns": "",
                    "connectivity_score": "",
                    "objective_score": "",
                    "objective_reward": "",
                    "reward_per_km": "",
                }
            )


def append_comparison(solution: dict, stations: list[dict]) -> None:
    metrics = solution_metrics(solution, stations)
    exists = COMPARISON_FILE.exists()
    rows = []
    if exists:
        with COMPARISON_FILE.open("r", encoding="utf-8-sig", newline="") as f:
            rows = [row for row in csv.DictReader(f) if row.get("method") != METHOD]
    rows.append(
        {
            "method": metrics["method"],
            "selected_stations": metrics["selected_stations"],
            "selected_edges": metrics["selected_edges"],
            "selected_required": metrics["selected_required"],
            "length_km": f'{metrics["length_km"]:.3f}',
            "pair_reward": f'{metrics["pair_reward"]:.6f}',
            "construction_cost": f'{metrics["construction_cost"]:.6f}',
            "turn_penalty": f'{metrics["turn_penalty"]:.6f}',
            "endpoint_count": metrics["endpoint_count"],
            "endpoint_penalty": f'{metrics["endpoint_penalty"]:.6f}',
            "max_turn_deg": f'{metrics["max_turn_deg"]:.3f}',
            "penalized_turns": metrics["penalized_turns"],
            "connectivity_score": f'{metrics["connectivity_score"]:.6f}',
            "objective_score": f'{metrics["objective_score"]:.6f}',
            "objective_reward": f'{metrics["objective_score"]:.6f}',
            "reward_per_km": f'{metrics["reward_per_km"]:.6f}',
            "station_ids": metrics["station_ids"],
        }
    )
    with COMPARISON_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "method",
                "selected_stations",
                "selected_edges",
                "selected_required",
                "length_km",
                "pair_reward",
                "construction_cost",
                "turn_penalty",
                "endpoint_count",
                "endpoint_penalty",
                "max_turn_deg",
                "penalized_turns",
                "connectivity_score",
                "objective_score",
                "objective_reward",
                "reward_per_km",
                "station_ids",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    if not STATION_TABLE_FILE.exists():
        raise FileNotFoundError(
            f"{STATION_TABLE_FILE} not found. Run: uv run python .\\line_network_model\\01_select_stations.py"
        )

    population = load_population()
    roads = load_roads()
    stations = load_station_file()
    solution, history = run_ga(stations)

    svg_path = output_path(METHOD)
    write_svg(population, stations, solution, roads, svg_path)
    write_ga_summary(solution, history, stations)
    append_comparison(solution, stations)

    metrics = solution_metrics(solution, stations)
    print("Genetic baseline complete")
    print(f"  population_size: {POPULATION_SIZE}")
    print(f"  generations: {GENERATIONS}")
    print(f"  station candidates: {len(stations)}")
    print(f"  selected stations: {metrics['selected_stations']}")
    print(f"  selected edges: {metrics['selected_edges']}")
    print(f"  length_km: {metrics['length_km']:.2f}")
    print(f"  pair_reward: {metrics['pair_reward']:.3f}")
    print(f"  construction_cost: {metrics['construction_cost']:.3f}")
    print(f"  turn_penalty: {metrics['turn_penalty']:.3f}")
    print(f"  endpoint_count: {metrics['endpoint_count']}")
    print(f"  endpoint_penalty: {metrics['endpoint_penalty']:.3f} (diagnostic only)")
    print(f"  objective_score: {metrics['objective_score']:.3f}")
    print(f"  outputs: {svg_path}, {SUMMARY_FILE}")


if __name__ == "__main__":
    main()
