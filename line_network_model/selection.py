"""Line selection algorithms for candidate line pools."""

from __future__ import annotations

import random

import networkx as nx
import numpy as np

from line_network_model.line_pool import line_od_pairs


def _selected_cost(lines: list[dict]) -> float:
    return float(sum(line["construction_cost"] for line in lines))


def _constraints_ok(lines: list[dict], max_lines: int | None, budget: float | None) -> bool:
    if max_lines is not None and len(lines) > max_lines:
        return False
    if budget is not None and _selected_cost(lines) > budget:
        return False
    return True


def _pair_weights(line_pool: list[dict], od_matrix: np.ndarray) -> dict[tuple[str, str], float]:
    ids = sorted({sid for line in line_pool for sid in line["station_sequence"]})
    # OD matrices are indexed by station-table order; line ids in this helper are only used by callers that
    # precompute candidate pair values via line attributes. Fall back to unit values if exact indices are unknown.
    _ = od_matrix, ids
    return {}


def _line_pairs(line: dict) -> set[tuple[str, str]]:
    return line.setdefault("_od_pairs", line_od_pairs(line["station_sequence"]))


def select_lines_greedy_od(line_pool: list[dict], od_matrix: np.ndarray, max_lines: int | None = None, budget: float | None = None) -> list[dict]:
    """Greedily select lines that maximize new direct OD coverage."""
    _ = od_matrix
    selected: list[dict] = []
    covered_pairs: set[tuple[str, str]] = set()
    remaining = list(line_pool)
    while remaining:
        best_line = None
        best_gain = 0.0
        for line in remaining:
            trial = selected + [line]
            if not _constraints_ok(trial, max_lines, budget):
                continue
            new_ratio = len(_line_pairs(line) - covered_pairs) / max(1, len(_line_pairs(line)))
            gain = float(line["direct_od_coverage"]) * new_ratio
            if gain > best_gain:
                best_gain, best_line = gain, line
        if best_line is None or best_gain <= 0:
            break
        selected.append(best_line)
        covered_pairs |= _line_pairs(best_line)
        remaining.remove(best_line)
        if max_lines is not None and len(selected) >= max_lines:
            break
    return selected


def select_lines_greedy_bcr(line_pool: list[dict], od_matrix: np.ndarray, max_lines: int | None = None, budget: float | None = None) -> list[dict]:
    """Greedily select lines by new OD benefit divided by construction cost."""
    _ = od_matrix
    selected: list[dict] = []
    covered_pairs: set[tuple[str, str]] = set()
    remaining = list(line_pool)
    while remaining:
        best_line = None
        best_score = 0.0
        for line in remaining:
            trial = selected + [line]
            if not _constraints_ok(trial, max_lines, budget):
                continue
            new_ratio = len(_line_pairs(line) - covered_pairs) / max(1, len(_line_pairs(line)))
            gain = float(line["direct_od_coverage"]) * new_ratio
            score = gain / max(float(line["construction_cost"]), 1e-9)
            if score > best_score:
                best_score, best_line = score, line
        if best_line is None or best_score <= 0:
            break
        selected.append(best_line)
        covered_pairs |= _line_pairs(best_line)
        remaining.remove(best_line)
        if max_lines is not None and len(selected) >= max_lines:
            break
    return selected


def _fitness(chrom: np.ndarray, line_pool: list[dict], max_lines: int | None, budget: float | None) -> float:
    selected = [line for bit, line in zip(chrom, line_pool) if bit]
    if not _constraints_ok(selected, max_lines, budget) or not selected:
        return -1e9
    covered_stations = {s for line in selected for s in line["station_sequence"]}
    covered_pairs = set().union(*(_line_pairs(line) for line in selected))
    cost = _selected_cost(selected)
    graph = nx.Graph()
    for line in selected:
        nx.add_path(graph, line["station_sequence"])
    components = nx.number_connected_components(graph) if graph.number_of_nodes() else 0
    connectivity_bonus = 1.0 / max(1, components)
    od_score = sum(line["direct_od_coverage"] for line in selected)
    pop_score = sum(line["covered_population"] for line in selected)
    return 4.0 * od_score + 0.000001 * pop_score + 0.02 * len(covered_pairs) + 0.5 * len(covered_stations) + connectivity_bonus - 0.0001 * cost


def select_lines_ga(
    line_pool: list[dict],
    od_matrix: np.ndarray,
    max_lines: int | None = None,
    budget: float | None = None,
    population_size: int = 100,
    generations: int = 200,
    seed: int = 42,
) -> list[dict]:
    """Select lines with a lightweight binary genetic algorithm implemented without optional dependencies."""
    _ = od_matrix
    rng = np.random.default_rng(seed)
    n = len(line_pool)
    if n == 0:
        return []
    max_selected = max_lines or min(n, 10)
    pop = np.zeros((population_size, n), dtype=bool)
    for row in pop:
        k = int(rng.integers(1, min(max_selected, n) + 1))
        row[rng.choice(n, size=k, replace=False)] = True
    best = pop[0].copy()
    best_fit = _fitness(best, line_pool, max_lines, budget)
    for _gen in range(generations):
        fits = np.array([_fitness(ch, line_pool, max_lines, budget) for ch in pop])
        elite_idx = np.argsort(fits)[-max(2, population_size // 8) :]
        if fits[elite_idx[-1]] > best_fit:
            best_fit = float(fits[elite_idx[-1]])
            best = pop[elite_idx[-1]].copy()
        next_pop = [pop[i].copy() for i in elite_idx]
        while len(next_pop) < population_size:
            p1, p2 = pop[rng.choice(elite_idx, size=2, replace=True)]
            cut = int(rng.integers(1, n))
            child = np.concatenate([p1[:cut], p2[cut:]])
            mutation = rng.random(n) < min(0.08, 3.0 / n)
            child = np.logical_xor(child, mutation)
            if not child.any():
                child[int(rng.integers(0, n))] = True
            next_pop.append(child)
        pop = np.array(next_pop, dtype=bool)
    selected = [line for bit, line in zip(best, line_pool) if bit]
    if not _constraints_ok(selected, max_lines, budget):
        selected = select_lines_greedy_bcr(line_pool, od_matrix, max_lines, budget)
    return selected


def select_lines_ilp(line_pool: list[dict], od_matrix: np.ndarray, max_lines: int | None = None, budget: float | None = None) -> list[dict]:
    """Integer-programming baseline using pulp when installed; otherwise falls back to greedy OD."""
    _ = od_matrix
    try:
        import pulp
    except ImportError:
        return select_lines_greedy_od(line_pool, od_matrix, max_lines, budget)

    prob = pulp.LpProblem("line_selection", pulp.LpMaximize)
    x = [pulp.LpVariable(f"x_{i}", cat="Binary") for i in range(len(line_pool))]
    prob += pulp.lpSum(x[i] * float(line["direct_od_coverage"]) for i, line in enumerate(line_pool))
    if max_lines is not None:
        prob += pulp.lpSum(x) <= max_lines
    if budget is not None:
        prob += pulp.lpSum(x[i] * float(line["construction_cost"]) for i, line in enumerate(line_pool)) <= budget
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[prob.status] not in {"Optimal", "Feasible"}:
        return select_lines_greedy_od(line_pool, od_matrix, max_lines, budget)
    return [line for var, line in zip(x, line_pool) if var.value() and var.value() > 0.5]

