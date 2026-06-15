"""Reproduction of Ahmed et al. 2020 GA for rail transit system planning.

Implements Stage 2 of the paper: simultaneous optimization of station locations
and line network using a Genetic Algorithm.

Key features from the paper:
  - Chromosome: whole solution; genes = rail lines; bits = stations
  - Fixed terminal stations per line (determined by planner / data-driven)
  - Tournament selection, uniform crossover, station-level mutation
  - Feasibility repair: reject infeasible matings/mutations
  - Fitness: total system cost = passenger + operator + community

Simplifications from the paper (documented in reproduction plan):
  - Stage 1 (GIS screening) → our 01_select_stations pipeline
  - Logit mode choice → gravity-model OD coverage
  - Leicester-specific cost params → configurable defaults
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd


# Default cost parameters (from Ahmed 2020 Table 1, scaled)
DEFAULT_COST_PARAMS = {
    "train_speed_kmh": 80.0,          # Vt
    "walk_speed_kmh": 4.0,            # Va
    "headway_min": 5.0,               # Hw
    "value_of_access_time": 14.0,     # A0 (£/hr) - scaled
    "value_of_waiting_time": 14.0,    # W0
    "value_of_in_vehicle_time": 7.0,  # T0
    "rail_op_cost_per_pax_km": 0.21,  # Mt0
    "bus_op_cost_per_pax_km": 0.16,   # Mb0
    "car_op_cost_per_pax_km": 0.26,   # Mc0
    "station_build_cost": 1_000_000,  # Sb (£)
    "tunnel_cost_per_km": 12_000_000, # CTu (£/km)
    "track_cost_per_m": 650,          # CTr (£/m)
    "passenger_coeff": 1.0,           # φp
    "operator_coeff": 1.0,            # φo
    "community_coeff": 1.0,           # φc
}

# Constraints (from Ahmed 2020 Table 1)
DEFAULT_CONSTRAINTS = {
    "min_stations_per_line": 4,
    "max_stations_per_line": 10,
    "min_station_spacing_m": 1000.0,
    "max_station_spacing_m": 3000.0,
    "min_transfer_stations": 1,      # per line
    "max_overlap_ratio": 0.30,
}

# GA parameters (from Ahmed 2020)
DEFAULT_GA_PARAMS = {
    "population_size": 200,      # scaled down from 500 (we have 54 vs 4774 candidates)
    "generations": 50,
    "tournament_size": 2,
    "crossover_rate": 0.7,
    "mutation_rate": 0.3,
    "elite_count": 4,
    "seed": 42,
}


@dataclass
class AhmedInstance:
    """Encapsulates a multi-line rail transit planning problem instance."""

    stations: pd.DataFrame
    od_matrix: np.ndarray
    corridor_graph: nx.Graph
    terminal_pairs: list[tuple[str, str]]  # one pair per line
    cost_params: dict = field(default_factory=lambda: dict(DEFAULT_COST_PARAMS))
    constraints: dict = field(default_factory=lambda: dict(DEFAULT_CONSTRAINTS))

    def __post_init__(self) -> None:
        ids = self.stations["station_id"].astype(str).tolist()
        self._id_to_idx = {sid: i for i, sid in enumerate(ids)}
        xy_df = self.stations.set_index("station_id")
        self._x = {sid: float(xy_df.loc[sid, "x"]) for sid in ids}
        self._y = {sid: float(xy_df.loc[sid, "y"]) for sid in ids}
        self._value = {sid: float(xy_df.loc[sid, "total_value"]) for sid in ids}
        self._pop = {sid: float(xy_df.loc[sid, "population_value"]) for sid in ids}
        self.all_ids = ids
        self.all_id_set = set(ids)

    @property
    def n_lines(self) -> int:
        return len(self.terminal_pairs)

    def edge_len(self, u: str, v: str) -> float:
        if self.corridor_graph.has_edge(u, v):
            return float(self.corridor_graph[u][v].get("distance", 0.0))
        return float(np.hypot(self._x[u] - self._x[v], self._y[u] - self._y[v]))

    def path_len(self, seq: list[str]) -> float:
        return sum(self.edge_len(seq[i], seq[i + 1]) for i in range(len(seq) - 1))

    def path_distance_matrix(self, seq: list[str]) -> np.ndarray:
        """Return (n,n) matrix of distances along the path sequence."""
        n = len(seq)
        cumul = np.zeros(n, dtype=float)
        for i in range(1, n):
            cumul[i] = cumul[i - 1] + self.edge_len(seq[i - 1], seq[i])
        dm = np.zeros((n, n), dtype=float)
        for i in range(n):
            for j in range(n):
                dm[i, j] = abs(cumul[j] - cumul[i])
        return dm

    def shortest_path_between(
        self, uid: str, vid: str, track_graph: nx.Graph
    ) -> float:
        """Shortest travel distance in the track network."""
        try:
            return float(
                nx.shortest_path_length(track_graph, uid, vid, weight="distance")
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return float("inf")


def _select_terminal_pairs(
    stations: pd.DataFrame,
    od_matrix: np.ndarray,
    n_lines: int,
    method: str = "top_od",
) -> list[tuple[str, str]]:
    """Select terminal station pairs for each line.

    Methods:
      'top_od' — highest OD pairs (data-driven, as paper allows)
      'top_value' — highest-value station pairs
    """
    ids = stations["station_id"].astype(str).tolist()
    id_to_idx = {sid: i for i, sid in enumerate(ids)}
    value = dict(zip(ids, stations["total_value"].astype(float)))

    pairs = []
    for u, v in itertools.combinations(ids, 2):
        if method == "top_od":
            score = float(od_matrix[id_to_idx[u], id_to_idx[v]]
                          + od_matrix[id_to_idx[v], id_to_idx[u]])
        else:
            score = value[u] + value[v]
        pairs.append((score, u, v))
    pairs.sort(key=lambda x: x[0], reverse=True)

    selected = []
    used: set[str] = set()
    for _score, u, v in pairs:
        if u in used or v in used:
            continue
        selected.append((u, v))
        used.add(u)
        used.add(v)
        if len(selected) >= n_lines:
            break
    return selected


def _check_constraints(
    instance: AhmedInstance,
    chromosome: list[list[str]],
) -> tuple[bool, str]:
    """Check if a chromosome satisfies all constraints from the paper.

    Returns (ok, reason).
    """
    c = instance.constraints
    all_stations: set[str] = set()
    all_edges: set[tuple[str, str]] = set()
    line_station_sets: list[set[str]] = []

    for line_seq in chromosome:
        n = len(line_seq)
        if n < c["min_stations_per_line"]:
            return False, f"line has {n} stations < min {c['min_stations_per_line']}"
        if n > c["max_stations_per_line"]:
            return False, f"line has {n} stations > max {c['max_stations_per_line']}"

        # Station spacing
        for a, b in zip(line_seq[:-1], line_seq[1:]):
            d = instance.edge_len(a, b)
            if d < c["min_station_spacing_m"]:
                return False, f"spacing {d:.0f}m < min {c['min_station_spacing_m']}m"
            if d > c["max_station_spacing_m"]:
                return False, f"spacing {d:.0f}m > max {c['max_station_spacing_m']}m"

        all_stations.update(line_seq)
        all_edges.update(
            tuple(sorted((line_seq[i], line_seq[i + 1])))
            for i in range(len(line_seq) - 1)
        )
        line_station_sets.append(set(line_seq))

    # Transfer stations: each line must share at least min_transfer stations with others
    for i, s_i in enumerate(line_station_sets):
        others = set().union(*(s for j, s in enumerate(line_station_sets) if j != i))
        if others and len(s_i & others) < c["min_transfer_stations"]:
            return False, f"line {i} has < {c['min_transfer_stations']} transfer stations"

    # Overlap between lines
    if len(line_station_sets) >= 2:
        for i in range(len(line_station_sets)):
            other_stations = set().union(
                *(s for j, s in enumerate(line_station_sets) if j != i)
            )
            overlap = len(line_station_sets[i] & other_stations) / max(
                len(line_station_sets[i]), 1
            )
            if overlap > c["max_overlap_ratio"]:
                return False, f"line {i} overlap {overlap:.1%} > max {c['max_overlap_ratio']:.0%}"

    return True, "ok"


def _build_track_graph(chromosome: list[list[str]], instance: AhmedInstance) -> nx.Graph:
    """Build union track graph from all lines."""
    g = nx.Graph()
    for line_seq in chromosome:
        for sid in line_seq:
            g.add_node(sid)
        for u, v in zip(line_seq[:-1], line_seq[1:]):
            d = instance.edge_len(u, v)
            g.add_edge(u, v, distance=d)
    return g


def _compute_fitness(
    chromosome: list[list[str]],
    instance: AhmedInstance,
) -> float:
    """Compute total system cost (to be minimized → negative for maximization).

    TotalCost = φp * PassengerCost + φo * OperatorCost + φc * CommunityCost

    Returns negative total cost (GA maximizes fitness = minimizes cost).
    Lower cost = better = higher fitness.
    """
    cp = instance.cost_params
    all_stations = set()
    total_line_length = 0.0

    for line_seq in chromosome:
        all_stations.update(line_seq)
        total_line_length += instance.path_len(line_seq)

    # Build track graph for shortest-path distances
    track_graph = _build_track_graph(chromosome, instance)

    # --- Passenger Cost ---
    # Savings from using rail vs. car: Σ OD_ij * (travel_time_car - travel_time_rail)
    # Travel time rail includes: access + waiting + in-vehicle
    # Simplified: use network distance / train_speed vs Euclidean / car_speed_kmh
    car_speed_kmh = 40.0  # urban car speed
    passenger_cost = 0.0
    n_stations = len(instance.all_ids)
    id_list = instance.all_ids

    for i in range(n_stations):
        for j in range(i + 1, n_stations):
            u, v = id_list[i], id_list[j]
            od_val = float(
                instance.od_matrix[i, j] + instance.od_matrix[j, i]
            )
            if od_val <= 0:
                continue
            # Car travel time (hours)
            euclid = float(
                np.hypot(
                    instance._x[u] - instance._x[v],
                    instance._y[u] - instance._y[v],
                )
            )
            car_time_h = euclid / (car_speed_kmh * 1000.0)

            # Rail travel time: access + wait + in-vehicle
            walk_dist_u = min(
                (instance.edge_len(u, s) for s in all_stations if s in track_graph),
                default=float("inf"),
            )
            walk_dist_v = min(
                (instance.edge_len(v, s) for s in all_stations if s in track_graph),
                default=float("inf"),
            )
            if walk_dist_u == float("inf") or walk_dist_v == float("inf"):
                # One or both not covered → no rail benefit
                continue

            access_time_h = (walk_dist_u + walk_dist_v) / (
                cp["walk_speed_kmh"] * 1000.0
            )
            wait_time_h = cp["headway_min"] / 60.0 / 2.0  # half headway

            # Find nearest covered stations and compute in-vehicle time
            nearest_u = min(
                all_stations,
                key=lambda s: instance.edge_len(u, s),
            )
            nearest_v = min(
                all_stations,
                key=lambda s: instance.edge_len(v, s),
            )
            rail_dist = instance.shortest_path_between(
                nearest_u, nearest_v, track_graph
            )
            if rail_dist == float("inf"):
                continue
            in_vehicle_time_h = rail_dist / (cp["train_speed_kmh"] * 1000.0)
            rail_time_h = access_time_h + wait_time_h + in_vehicle_time_h

            # Time saving: car - rail (positive = rail saves time)
            time_saving_h = car_time_h - rail_time_h
            if time_saving_h > 0:
                passenger_cost -= (
                    od_val
                    * time_saving_h
                    * cp["value_of_in_vehicle_time"]
                )

    # --- Operator Cost ---
    # Savings: (bus_op_cost - rail_op_cost) * passenger_km
    total_passenger_km = 0.0
    for i in range(n_stations):
        for j in range(i + 1, n_stations):
            u, v = id_list[i], id_list[j]
            od_val = float(instance.od_matrix[i, j] + instance.od_matrix[j, i])
            nearest_u = min(
                (s for s in all_stations if s in track_graph),
                key=lambda s: instance.edge_len(u, s),
                default=None,
            )
            nearest_v = min(
                (s for s in all_stations if s in track_graph),
                key=lambda s: instance.edge_len(v, s),
                default=None,
            )
            if nearest_u and nearest_v:
                rd = instance.shortest_path_between(nearest_u, nearest_v, track_graph)
                if rd != float("inf"):
                    total_passenger_km += od_val * rd / 1000.0

    op_saving_per_km = (
        cp["bus_op_cost_per_pax_km"] - cp["rail_op_cost_per_pax_km"]
    )
    operator_cost = -op_saving_per_km * total_passenger_km  # negative = savings

    # --- Community Cost ---
    n_total_stations = len(all_stations)
    community_cost = (
        n_total_stations * cp["station_build_cost"]
        + (total_line_length / 1000.0) * cp["tunnel_cost_per_km"]
        + total_line_length * cp["track_cost_per_m"]
    )

    total_cost = (
        cp["passenger_coeff"] * passenger_cost
        + cp["operator_coeff"] * operator_cost
        + cp["community_coeff"] * community_cost
    )

    return -total_cost  # maximize fitness = minimize cost


def _generate_chromosome(
    instance: AhmedInstance,
    rng: np.random.Generator,
) -> list[list[str]]:
    """Generate a random feasible chromosome.

    Each line: terminal_a + random intermediate stations + terminal_b.
    Intermediate stations are selected from feasible candidates.
    """
    c = instance.constraints
    all_ids = set(instance.all_ids)
    chromosome = []

    for term_a, term_b in instance.terminal_pairs:
        # Exclude terminals from intermediate pool
        pool = list(all_ids - {term_a, term_b})
        rng.shuffle(pool)

        # Build line: start with terminal_a, add intermediates, end with terminal_b
        line = [term_a]
        for sid in pool:
            if len(line) >= c["max_stations_per_line"] - 1:
                break
            # Check spacing with last added station
            if instance.edge_len(line[-1], sid) >= c["min_station_spacing_m"]:
                line.append(sid)
        line.append(term_b)

        # Ensure min stations
        while len(line) < c["min_stations_per_line"]:
            for sid in pool:
                if sid not in line:
                    if instance.edge_len(line[-2], sid) >= c["min_station_spacing_m"]:
                        line.insert(-1, sid)
                        break
            else:
                break  # can't add more

        chromosome.append(line)

    return chromosome


def _tournament_select(
    population: list,
    fitnesses: np.ndarray,
    rng: np.random.Generator,
    tournament_size: int,
) -> int:
    """Tournament selection: pick the best among 'tournament_size' random individuals."""
    contenders = rng.choice(len(population), size=min(tournament_size, len(population)), replace=False)
    return int(contenders[np.argmax(fitnesses[contenders])])


def _uniform_crossover(
    parent_a: list[list[str]],
    parent_b: list[list[str]],
    instance: AhmedInstance,
    rng: np.random.Generator,
) -> list[list[str]]:
    """Uniform crossover at the station (bit) level with feasibility repair.

    For each line and each position, swap stations between parents with 50% prob.
    If swap creates infeasibility, revert the swap.
    """
    child = [[s for s in line] for line in parent_a]
    for li in range(len(child)):
        for pos in range(1, len(child[li]) - 1):  # skip terminals
            if rng.random() < 0.5:
                if pos < len(parent_b[li]) - 1:
                    # Try swap
                    orig = child[li][pos]
                    child[li][pos] = parent_b[li][pos]
                    ok, _ = _check_constraints(instance, child)
                    if not ok:
                        child[li][pos] = orig  # revert
    return child


def _mutate(
    chromosome: list[list[str]],
    instance: AhmedInstance,
    rng: np.random.Generator,
    mutation_rate: float,
) -> None:
    """Station-level mutation with feasibility repair.

    Replace stations with randomly selected alternatives from the candidate pool.
    Reject mutations that violate constraints.
    """
    pool = list(instance.all_id_set)
    for li, line in enumerate(chromosome):
        for pos in range(1, len(line) - 1):  # skip terminals
            if rng.random() < mutation_rate:
                orig = line[pos]
                new_station = rng.choice(pool)
                if new_station == orig:
                    continue
                line[pos] = new_station
                ok, _ = _check_constraints(instance, chromosome)
                if not ok:
                    line[pos] = orig  # revert


def select_lines_ahmed_ga(
    instance: AhmedInstance,
    ga_params: dict | None = None,
    verbose: bool = True,
) -> dict:
    """Run the Ahmed 2020 GA to select optimal station locations and line network.

    Returns a dict with:
      - chromosome: best chromosome found
      - fitness_history: list of (generation, best_fitness, mean_fitness)
      - runtime_s: total wall-clock time
      - final_fitness: fitness of the best solution
      - n_stations_selected: number of unique stations used
      - total_length_km: total line network length
    """
    gp = {**DEFAULT_GA_PARAMS, **(ga_params or {})}
    rng = np.random.default_rng(gp["seed"])
    t0 = time.perf_counter()

    # Initialize population
    population = [
        _generate_chromosome(instance, rng)
        for _ in range(gp["population_size"])
    ]

    best_chromosome = None
    best_fitness = float("-inf")
    history = []

    for gen in range(gp["generations"] + 1):
        # Evaluate fitness
        fitnesses = np.array([
            _compute_fitness(chrom, instance) for chrom in population
        ])
        gen_best_idx = int(np.argmax(fitnesses))
        gen_best = float(fitnesses[gen_best_idx])
        gen_mean = float(np.mean(fitnesses))

        if gen_best > best_fitness:
            best_fitness = gen_best
            best_chromosome = [
                [s for s in line] for line in population[gen_best_idx]
            ]

        if gen % 10 == 0 or gen == gp["generations"]:
            history.append((gen, -gen_best, -gen_mean))

        if gen == gp["generations"]:
            break

        # Selection + Crossover + Mutation
        ranked = np.argsort(fitnesses)[::-1]
        next_pop = [
            [s for s in line]
            for idx in ranked[: gp["elite_count"]]
            for line in population[idx]
        ]
        # Reshape elites
        next_pop_chroms = []
        per_chrom = instance.n_lines
        for i in range(gp["elite_count"]):
            chrom = population[ranked[i]]
            next_pop_chroms.append([[s for s in line] for line in chrom])

        while len(next_pop_chroms) < gp["population_size"]:
            pa_idx = _tournament_select(population, fitnesses, rng, gp["tournament_size"])
            pb_idx = _tournament_select(population, fitnesses, rng, gp["tournament_size"])
            if rng.random() < gp["crossover_rate"]:
                child = _uniform_crossover(
                    population[pa_idx], population[pb_idx], instance, rng
                )
            else:
                child = [[s for s in line] for line in population[pa_idx]]
            _mutate(child, instance, rng, gp["mutation_rate"])
            ok, _ = _check_constraints(instance, child)
            if ok:
                next_pop_chroms.append(child)
            else:
                next_pop_chroms.append(
                    [[s for s in line] for line in population[pa_idx]]
                )

        population = next_pop_chroms

    elapsed = time.perf_counter() - t0
    total_len = sum(
        instance.path_len(line) for line in best_chromosome
    ) if best_chromosome else 0.0
    all_stations = (
        {s for line in best_chromosome for s in line}
        if best_chromosome
        else set()
    )

    return {
        "chromosome": best_chromosome,
        "fitness_history": history,
        "runtime_s": elapsed,
        "final_fitness": -best_fitness,  # convert back to cost
        "n_stations_selected": len(all_stations),
        "total_length_km": total_len / 1000.0,
        "all_stations": all_stations,
        "track_graph": (
            _build_track_graph(best_chromosome, instance)
            if best_chromosome
            else nx.Graph()
        ),
    }


def run_multi_line_experiment(
    stations: pd.DataFrame,
    od_matrix: np.ndarray,
    corridor_graph: nx.Graph,
    n_lines: int = 1,
    ga_params: dict | None = None,
) -> dict:
    """Run Ahmed 2020 GA for a given number of lines."""
    terminals = _select_terminal_pairs(stations, od_matrix, n_lines)
    instance = AhmedInstance(
        stations=stations,
        od_matrix=od_matrix,
        corridor_graph=corridor_graph,
        terminal_pairs=terminals,
    )
    return select_lines_ahmed_ga(instance, ga_params=ga_params)
