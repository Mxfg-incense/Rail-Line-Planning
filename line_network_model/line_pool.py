"""Candidate line-pool generation and line metric helpers."""

from __future__ import annotations

import itertools
import random

import networkx as nx
import numpy as np
import pandas as pd


def station_index(stations: pd.DataFrame) -> dict[str, int]:
    """Map station_id to OD matrix row/column index."""
    return {str(sid): i for i, sid in enumerate(stations["station_id"].astype(str))}


def line_edges(sequence: list[str]) -> set[tuple[str, str]]:
    """Return undirected adjacent track edges used by a station sequence."""
    return {tuple(sorted((str(a), str(b)))) for a, b in zip(sequence[:-1], sequence[1:])}


def line_od_pairs(sequence: list[str]) -> set[tuple[str, str]]:
    """Return unordered station pairs directly covered by one line."""
    return {tuple(sorted(pair)) for pair in itertools.combinations(map(str, sequence), 2)}


def path_length(graph: nx.Graph, sequence: list[str]) -> float:
    """Calculate the corridor distance along a station sequence."""
    total = 0.0
    for u, v in zip(sequence[:-1], sequence[1:]):
        total += float(graph[u][v].get("distance", graph[u][v].get("weight", 0.0)))
    return total


def _od_pair_value(od_matrix: np.ndarray, idx: dict[str, int], u: str, v: str) -> float:
    i, j = idx[u], idx[v]
    return float(od_matrix[i, j] + od_matrix[j, i])


def make_line(
    line_id: str,
    sequence: list[str],
    stations: pd.DataFrame,
    corridor_graph: nx.Graph,
    od_matrix: np.ndarray,
) -> dict:
    """Create a candidate-line dictionary with length, coverage, and cost attributes."""
    idx = station_index(stations)
    sequence = [str(s) for s in sequence]
    length = path_length(corridor_graph, sequence)
    pop_by_id = stations.set_index("station_id")["population_value"].to_dict()
    covered_population = float(sum(pop_by_id.get(sid, 0.0) for sid in set(sequence)))
    direct_od = sum(_od_pair_value(od_matrix, idx, u, v) for u, v in line_od_pairs(sequence))
    cost = 0.0
    for u, v in zip(sequence[:-1], sequence[1:]):
        cost += float(corridor_graph[u][v].get("construction_cost", 0.0))
    return {
        "line_id": line_id,
        "station_sequence": sequence,
        "length": float(length),
        "covered_population": covered_population,
        "direct_od_coverage": float(direct_od),
        "construction_cost": float(cost),
    }


def deduplicate_and_filter_lines(
    raw_sequences: list[list[str]],
    stations: pd.DataFrame,
    corridor_graph: nx.Graph,
    od_matrix: np.ndarray,
    min_len: int = 4,
    max_len: int = 18,
) -> list[dict]:
    """Remove duplicate/reversed lines and filter by station count."""
    seen: set[tuple[str, ...]] = set()
    lines: list[dict] = []
    for seq in raw_sequences:
        seq = [str(s) for s in seq]
        if not (min_len <= len(seq) <= max_len):
            continue
        key = tuple(seq)
        rkey = tuple(reversed(seq))
        canonical = min(key, rkey)
        if canonical in seen:
            continue
        seen.add(canonical)
        lines.append(make_line(f"L{len(lines) + 1:04d}", seq, stations, corridor_graph, od_matrix))
    return lines


def generate_lines_by_top_od_pairs(
    stations: pd.DataFrame,
    corridor_graph: nx.Graph,
    od_matrix: np.ndarray,
    top_m: int = 100,
    min_len: int = 4,
    max_len: int = 18,
) -> list[dict]:
    """Generate shortest-path lines from the highest-demand OD station pairs."""
    ids = stations["station_id"].astype(str).tolist()
    values = []
    for i, j in itertools.combinations(range(len(ids)), 2):
        values.append((float(od_matrix[i, j] + od_matrix[j, i]), ids[i], ids[j]))
    raw = []
    for _, u, v in sorted(values, reverse=True)[:top_m]:
        try:
            raw.append(nx.shortest_path(corridor_graph, u, v, weight="distance"))
        except nx.NetworkXNoPath:
            continue
    return deduplicate_and_filter_lines(raw, stations, corridor_graph, od_matrix, min_len, max_len)


def generate_lines_by_terminal_pairs(
    stations: pd.DataFrame,
    corridor_graph: nx.Graph,
    top_terminal_num: int = 20,
    min_len: int = 4,
    max_len: int = 18,
) -> list[dict]:
    """Generate shortest-path lines between high-value terminal station candidates."""
    top = stations.sort_values("total_value", ascending=False).head(top_terminal_num)
    raw = []
    for u, v in itertools.combinations(top["station_id"].astype(str), 2):
        try:
            raw.append(nx.shortest_path(corridor_graph, u, v, weight="distance"))
        except nx.NetworkXNoPath:
            continue
    dummy_od = np.zeros((len(stations), len(stations)))
    return deduplicate_and_filter_lines(raw, stations, corridor_graph, dummy_od, min_len, max_len)


def _random_walk_sequence(graph: nx.Graph, start: str, rng: random.Random, max_len: int) -> list[str]:
    seq = [start]
    visited = {start}
    while len(seq) < max_len:
        neighbors = [n for n in graph.neighbors(seq[-1]) if n not in visited]
        if not neighbors:
            break
        neighbors.sort(key=lambda n: graph[seq[-1]][n].get("distance", 0.0))
        weights = np.array([1.0 / max(graph[seq[-1]][n].get("distance", 1.0), 1e-6) for n in neighbors], dtype=float)
        weights = weights / weights.sum()
        nxt = rng.choices(neighbors, weights=weights.tolist(), k=1)[0]
        seq.append(str(nxt))
        visited.add(str(nxt))
        if len(seq) >= 4 and rng.random() < 0.2:
            break
    return seq


def generate_lines_with_random_walk(
    stations: pd.DataFrame,
    corridor_graph: nx.Graph,
    num_lines: int = 200,
    min_len: int = 4,
    max_len: int = 12,
    seed: int = 42,
) -> list[dict]:
    """Generate plausible non-shortest lines by biased random walks on the corridor graph."""
    rng = random.Random(seed)
    ids = stations["station_id"].astype(str).tolist()
    weights = stations["total_value"].to_numpy(float)
    weights = (weights / weights.sum()).tolist() if weights.sum() > 0 else None
    raw = []
    for _ in range(num_lines):
        start = rng.choices(ids, weights=weights, k=1)[0] if weights else rng.choice(ids)
        seq = _random_walk_sequence(corridor_graph, start, rng, max_len)
        if rng.random() < 0.5:
            seq = list(reversed(seq))
        raw.append(seq)
    dummy_od = np.zeros((len(stations), len(stations)))
    return deduplicate_and_filter_lines(raw, stations, corridor_graph, dummy_od, min_len, max_len)


def generate_candidate_line_pool(
    stations: pd.DataFrame,
    corridor_graph: nx.Graph,
    od_matrix: np.ndarray,
    top_m: int = 120,
    top_terminal_num: int = 20,
    random_lines: int = 200,
    min_len: int = 4,
    max_len: int = 18,
) -> list[dict]:
    """Combine all line generation strategies into one deduplicated candidate pool."""
    raw_sequences: list[list[str]] = []
    for line in generate_lines_by_top_od_pairs(stations, corridor_graph, od_matrix, top_m, min_len, max_len):
        raw_sequences.append(line["station_sequence"])
    terminal_dummy = generate_lines_by_terminal_pairs(stations, corridor_graph, top_terminal_num, min_len, max_len)
    raw_sequences.extend(line["station_sequence"] for line in terminal_dummy)
    random_dummy = generate_lines_with_random_walk(stations, corridor_graph, random_lines, min_len, min(max_len, 12))
    raw_sequences.extend(line["station_sequence"] for line in random_dummy)
    return deduplicate_and_filter_lines(raw_sequences, stations, corridor_graph, od_matrix, min_len, max_len)

