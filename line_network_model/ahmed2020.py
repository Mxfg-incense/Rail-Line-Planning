"""Reproduction of Ahmed et al. 2020 GA for rail transit system planning.

Implements Stage 2 of the paper: simultaneous optimization of station locations
and line network using a Genetic Algorithm.

Key features from the paper:
  - Chromosome: whole solution; genes = rail lines; bits = stations
  - Fixed terminal stations per line
  - Tournament selection, uniform crossover, station-level mutation
  - Feasibility repair: reject infeasible matings/mutations
  - Fitness: total system cost = passenger + operator + community + geometry

Improvements over initial version for geometric realism:
  - Initialization via shortest paths on corridor graph (not random)
  - Mutation restricted to k-nearest neighbors (not any station)
  - Turn-angle penalty discourages zigzags
  - Self-intersection check per line
  - Track overlap penalty between lines
"""

from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd


DEFAULT_COST_PARAMS = {
    # Passenger value weights (primary driver of fitness)
    "value_connection_weight": 1.0,      # weight for pairwise value connection reward
    "value_connection_cutoff_m": 30_000.0,  # distance decay cutoff
    "od_coverage_weight": 100.0,         # weight for OD pair coverage
    # Construction cost weights (secondary, soft constraints)
    "construction_cost_per_m": 10.0,     # much lower than real to let value drive decisions
    "station_cost": 100_000.0,           # per-station cost
    # Geometry penalties
    "turn_penalty_weight": 5e5,
    "turn_threshold_deg": 90.0,
    "overlap_penalty_weight": 1e6,
    "self_intersect_penalty": 1e9,
}

DEFAULT_CONSTRAINTS = {
    "min_stations_per_line": 4,
    "max_stations_per_line": 14,
    "min_station_spacing_m": 800.0,
    "max_station_spacing_m": float("inf"),  # no max spacing for sparse stations
    "min_transfer_stations": 0,
    "max_overlap_ratio": 0.50,
}

DEFAULT_GA_PARAMS = {
    "population_size": 100,
    "generations": 40,
    "tournament_size": 3,
    "crossover_rate": 0.7,
    "mutation_rate": 0.3,
    "elite_count": 4,
    "seed": 42,
}


def _station_xy(instance: "AhmedInstance", sid: str) -> tuple[float, float]:
    return instance._x[sid], instance._y[sid]


def _turn_angle_deg(
    instance: "AhmedInstance", prev_sid: str, center_sid: str, next_sid: str
) -> float:
    """Turn angle at center_sid (0 = straight, 180 = U-turn)."""
    px, py = _station_xy(instance, prev_sid)
    cx, cy = _station_xy(instance, center_sid)
    nx, ny = _station_xy(instance, next_sid)
    v1 = (px - cx, py - cy)
    v2 = (nx - cx, ny - cy)
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    cos_theta = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return 180.0 - math.degrees(math.acos(cos_theta))


def _segments_intersect(
    instance: "AhmedInstance",
    a1: str, a2: str, b1: str, b2: str,
) -> bool:
    """Check if two line segments intersect (excluding shared endpoints)."""
    if {a1, a2} & {b1, b2}:
        return False  # adjacent edges share a node — that's a turn, not an intersect
    x1, y1 = _station_xy(instance, a1)
    x2, y2 = _station_xy(instance, a2)
    x3, y3 = _station_xy(instance, b1)
    x4, y4 = _station_xy(instance, b2)

    def _ccw(ax, ay, bx, by, cx, cy):
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    d1 = _ccw(x1, y1, x2, y2, x3, y3)
    d2 = _ccw(x1, y1, x2, y2, x4, y4)
    d3 = _ccw(x3, y3, x4, y4, x1, y1)
    d4 = _ccw(x3, y3, x4, y4, x2, y2)

    if (d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0):
        if (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0):
            return True
    # Collinear cases
    eps = 1e-9
    if abs(d1) < eps:
        if min(x1, x2) <= x3 <= max(x1, x2) and min(y1, y2) <= y3 <= max(y1, y2):
            return True
    if abs(d2) < eps:
        if min(x1, x2) <= x4 <= max(x1, x2) and min(y1, y2) <= y4 <= max(y1, y2):
            return True
    if abs(d3) < eps:
        if min(x3, x4) <= x1 <= max(x3, x4) and min(y3, y4) <= y1 <= max(y3, y4):
            return True
    if abs(d4) < eps:
        if min(x3, x4) <= x2 <= max(x3, x4) and min(y3, y4) <= y2 <= max(y3, y4):
            return True
    return False


def _count_self_intersections(
    instance: "AhmedInstance", seq: list[str]
) -> int:
    """Count self-intersections in a line sequence."""
    count = 0
    edges = list(zip(seq[:-1], seq[1:]))
    for i in range(len(edges)):
        for j in range(i + 2, len(edges)):
            if j == i + 1:
                continue  # adjacent edges
            if _segments_intersect(instance, edges[i][0], edges[i][1],
                                    edges[j][0], edges[j][1]):
                count += 1
    return count


@dataclass
class AhmedInstance:
    """Encapsulates a multi-line rail transit planning problem instance."""

    stations: pd.DataFrame
    od_matrix: np.ndarray
    corridor_graph: nx.Graph
    terminal_pairs: list[tuple[str, str]]
    cost_params: dict = field(default_factory=lambda: dict(DEFAULT_COST_PARAMS))
    constraints: dict = field(default_factory=lambda: dict(DEFAULT_CONSTRAINTS))

    def __post_init__(self) -> None:
        ids = self.stations["station_id"].astype(str).tolist()
        self._id_to_idx = {sid: i for i, sid in enumerate(ids)}
        xy_df = self.stations.set_index("station_id")
        self._x = {sid: float(xy_df.loc[sid, "x"]) for sid in ids}
        self._y = {sid: float(xy_df.loc[sid, "y"]) for sid in ids}
        self._value = {sid: float(xy_df.loc[sid, "total_value"]) for sid in ids}
        self.all_ids = ids
        self.all_id_set = set(ids)
        # Precompute k-nearest neighbors for mutation
        self._knn: dict[str, list[str]] = {}
        self._build_knn(k=8)

    def _build_knn(self, k: int) -> None:
        for sid in self.all_ids:
            dists = []
            for other in self.all_ids:
                if other == sid:
                    continue
                d = float(np.hypot(
                    self._x[sid] - self._x[other],
                    self._y[sid] - self._y[other],
                ))
                dists.append((d, other))
            dists.sort(key=lambda x: x[0])
            self._knn[sid] = [other for _, other in dists[:k]]

    @property
    def n_lines(self) -> int:
        return len(self.terminal_pairs)

    def edge_len(self, u: str, v: str) -> float:
        if self.corridor_graph.has_edge(u, v):
            return float(self.corridor_graph[u][v].get("distance", 0.0))
        return float(np.hypot(self._x[u] - self._x[v], self._y[u] - self._y[v]))

    def path_len(self, seq: list[str]) -> float:
        return sum(self.edge_len(seq[i], seq[i + 1]) for i in range(len(seq) - 1))

    def shortest_path_between(
        self, uid: str, vid: str, track_graph: nx.Graph
    ) -> float:
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
    used_terminals: set[str] = set()

    # First line: highest-OD pair
    selected.append((pairs[0][1], pairs[0][2]))
    used_terminals.update(selected[0])

    # Subsequent lines: prefer sharing a terminal (hub-based network)
    for _ in range(n_lines - 1):
        hub_pair = None
        for score, u, v in pairs:
            if any({u, v} == set(p) for p in selected):
                continue
            # Share exactly one terminal → creates a hub
            if (u in used_terminals) != (v in used_terminals):
                hub_pair = (score, u, v)
                break
        # Fallback: any unselected pair
        if hub_pair is None:
            for score, u, v in pairs:
                if not any({u, v} == set(p) for p in selected):
                    hub_pair = (score, u, v)
                    break
        if hub_pair is None:
            break
        selected.append((hub_pair[1], hub_pair[2]))
        used_terminals.update(selected[-1])

    return selected


def _check_constraints(
    instance: AhmedInstance,
    chromosome: list[list[str]],
) -> tuple[bool, str]:
    c = instance.constraints
    line_station_sets: list[set[str]] = []

    for li, line_seq in enumerate(chromosome):
        n = len(line_seq)
        if n < c["min_stations_per_line"]:
            return False, f"line {li}: {n} stations < min {c['min_stations_per_line']}"
        if n > c["max_stations_per_line"]:
            return False, f"line {li}: {n} stations > max {c['max_stations_per_line']}"

        # Duplicate stations in a line = not a simple path
        if len(set(line_seq)) != len(line_seq):
            return False, f"line {li}: duplicate stations"

        for a, b in zip(line_seq[:-1], line_seq[1:]):
            d = instance.edge_len(a, b)
            if d < c["min_station_spacing_m"]:
                return False, f"line {li}: spacing {d:.0f}m < min {c['min_station_spacing_m']}m"
            if d > c["max_station_spacing_m"]:
                return False, f"line {li}: spacing {d:.0f}m > max {c['max_station_spacing_m']}m"

        # No self-intersection
        if _count_self_intersections(instance, line_seq) > 0:
            return False, f"line {li}: self-intersects"

        line_station_sets.append(set(line_seq))

    # Transfer stations
    for i, s_i in enumerate(line_station_sets):
        others = set().union(*(s for j, s in enumerate(line_station_sets) if j != i))
        if others and len(s_i & others) < c["min_transfer_stations"]:
            return False, f"line {i}: < {c['min_transfer_stations']} transfer stations"

    # Max overlap
    if len(line_station_sets) >= 2:
        for i in range(len(line_station_sets)):
            other_stations = set().union(
                *(s for j, s in enumerate(line_station_sets) if j != i)
            )
            overlap = len(line_station_sets[i] & other_stations) / max(
                len(line_station_sets[i]), 1
            )
            if overlap > c["max_overlap_ratio"]:
                return False, f"line {i}: overlap {overlap:.1%} > max"

    return True, "ok"


def _build_track_graph(
    chromosome: list[list[str]], instance: AhmedInstance
) -> nx.Graph:
    g = nx.Graph()
    for line_seq in chromosome:
        for sid in line_seq:
            g.add_node(sid)
        for u, v in zip(line_seq[:-1], line_seq[1:]):
            d = instance.edge_len(u, v)
            g.add_edge(u, v, distance=d)
    return g


def _compute_geometry_penalty(
    chromosome: list[list[str]], instance: AhmedInstance
) -> float:
    """Penalty for turn angles + line overlaps."""
    cp = instance.cost_params
    penalty = 0.0

    # Turn penalty (per line)
    for line_seq in chromosome:
        if len(line_seq) < 3:
            continue
        turns = []
        for a, b, c in zip(line_seq[:-2], line_seq[1:-1], line_seq[2:]):
            angle = _turn_angle_deg(instance, a, b, c)
            excess = max(0.0, angle - cp["turn_threshold_deg"])
            turns.append((excess / 90.0) ** 2)
        penalty += cp["turn_penalty_weight"] * sum(turns)

    # Self-intersection penalty (hard via constraints, soft backup)
    for line_seq in chromosome:
        n_intersect = _count_self_intersections(instance, line_seq)
        penalty += n_intersect * cp["self_intersect_penalty"]

    # Track overlap penalty: penalize non-adjacent edges from different lines
    # that are very close to each other
    if len(chromosome) >= 2:
        all_edges: list[tuple[str, str, int]] = []
        for li, line_seq in enumerate(chromosome):
            for u, v in zip(line_seq[:-1], line_seq[1:]):
                all_edges.append((u, v, li))

        for i in range(len(all_edges)):
            for j in range(i + 1, len(all_edges)):
                u1, v1, l1 = all_edges[i]
                u2, v2, l2 = all_edges[j]
                if l1 == l2:
                    continue  # same line
                # Check if these edges are very close (overlapping corridors)
                d1 = instance.edge_len(u1, u2)
                d2 = instance.edge_len(v1, v2)
                if d1 < 500 and d2 < 500:
                    penalty += cp["overlap_penalty_weight"]

    return penalty


def _compute_fitness(
    chromosome: list[list[str]],
    instance: AhmedInstance,
) -> float:
    """Fitness = value_connection_reward + OD_coverage - construction_cost - geometry_penalty.

    Primary driver: pairwise value connection with distance decay (like Laporte 2005).
    This gives the GA a meaningful gradient to climb.
    """
    cp = instance.cost_params
    cutoff = cp["value_connection_cutoff_m"]

    all_stations: set[str] = set()
    total_line_length = 0.0

    for line_seq in chromosome:
        all_stations.update(line_seq)
        total_line_length += instance.path_len(line_seq)

    track_graph = _build_track_graph(chromosome, instance)

    # --- Value Connection Reward (Laporte-style) ---
    # Sum over all selected station pairs: v_i * v_j * max(0, 1 - D_ij / cutoff)
    sel_list = sorted(all_stations)
    value_reward = 0.0
    for i in range(len(sel_list)):
        for j in range(i + 1, len(sel_list)):
            u, v = sel_list[i], sel_list[j]
            vu, vv = instance._value.get(u, 0.0), instance._value.get(v, 0.0)
            d = instance.shortest_path_between(u, v, track_graph)
            if d == float("inf"):
                continue  # not connected
            decay = max(0.0, 1.0 - d / cutoff)
            value_reward += vu * vv * decay

    # --- OD Coverage Reward ---
    # OD pairs where both origin and destination are covered by a station
    od_reward = 0.0
    n_stns = len(instance.all_ids)
    id_list = instance.all_ids
    for i in range(n_stns):
        for j in range(i + 1, n_stns):
            u, v = id_list[i], id_list[j]
            od_val = float(instance.od_matrix[i, j] + instance.od_matrix[j, i])
            if od_val <= 0:
                continue
            # Check if both have a station within walking distance
            walk_u = min(
                (instance.edge_len(u, s) for s in all_stations),
                default=float("inf"),
            )
            walk_v = min(
                (instance.edge_len(v, s) for s in all_stations),
                default=float("inf"),
            )
            if walk_u < 2000 and walk_v < 2000:  # within 2km access
                od_reward += od_val

    # --- Construction Cost ---
    construction_cost = (
        cp["construction_cost_per_m"] * total_line_length
        + cp["station_cost"] * len(all_stations)
    )

    # --- Geometry Penalty ---
    geometry_penalty = _compute_geometry_penalty(chromosome, instance)

    fitness = (
        cp["value_connection_weight"] * value_reward
        + cp["od_coverage_weight"] * od_reward
        - construction_cost
        - geometry_penalty
    )

    return fitness


# ---------------------------------------------------------------------------
# Chromosome generation via shortest paths (not random)
# ---------------------------------------------------------------------------

def _generate_chromosome_sp(
    instance: AhmedInstance,
    rng: np.random.Generator,
) -> list[list[str]]:
    """Generate a chromosome by finding a shortest path between terminals
    on the corridor graph, then enriching with intermediate stations.

    If the corridor shortest path has too few stations, we build a route
    by inserting intermediate stations that stay close to the direct
    corridor between the terminals.
    """
    c = instance.constraints
    chromosome = []

    for term_a, term_b in instance.terminal_pairs:
        try:
            base_path = nx.shortest_path(
                instance.corridor_graph, term_a, term_b, weight="distance"
            )
        except nx.NetworkXNoPath:
            base_path = [term_a, term_b]

        # If the path is too short, enrich it with intermediate stations
        if len(base_path) < c["min_stations_per_line"]:
            base_path = _enrich_path(instance, base_path, c, rng)

        # If too long, subsample
        if len(base_path) > c["max_stations_per_line"]:
            step = max(1, (len(base_path) - 2) // (c["max_stations_per_line"] - 2))
            indices = [0] + list(range(1, len(base_path) - 1, step)) + [len(base_path) - 1]
            base_path = [base_path[i] for i in indices[: c["max_stations_per_line"]]]

        # Add diversity: replace 1-2 stations with nearby alternatives
        n_swaps = rng.integers(0, 3) if len(base_path) > 5 else rng.integers(0, 2)
        for _ in range(n_swaps):
            if len(base_path) <= 4:
                break
            pos = rng.integers(1, len(base_path) - 1)
            orig = base_path[pos]
            neighbors = instance._knn.get(orig, [])
            if neighbors:
                rng.shuffle(neighbors)
                for alt in neighbors[:5]:
                    if alt not in base_path:
                        base_path[pos] = alt
                        break

        chromosome.append(base_path)

    return chromosome


def _enrich_path(
    instance: AhmedInstance,
    path: list[str],
    c: dict,
    rng: np.random.Generator,
) -> list[str]:
    """Insert intermediate stations to reach min_stations_per_line.

    Prefer stations that lie close to the direct corridor between terminals
    and have high station value.
    """
    term_a, term_b = path[0], path[-1]
    candidates = [
        s for s in instance.all_ids
        if s not in path
        and instance.edge_len(s, term_a) > c["min_station_spacing_m"]
        and instance.edge_len(s, term_b) > c["min_station_spacing_m"]
    ]
    if not candidates:
        return path

    xa, ya = instance._x[term_a], instance._y[term_a]
    xb, yb = instance._x[term_b], instance._y[term_b]
    seg_len_sq = (xb - xa) ** 2 + (yb - ya) ** 2

    def _dist_to_segment(sid: str) -> float:
        xs, ys = instance._x[sid], instance._y[sid]
        if seg_len_sq < 1e-9:
            return float(np.hypot(xs - xa, ys - ya))
        t = max(0.0, min(1.0,
            ((xs - xa) * (xb - xa) + (ys - ya) * (yb - ya)) / seg_len_sq))
        proj_x = xa + t * (xb - xa)
        proj_y = ya + t * (yb - ya)
        return float(np.hypot(xs - proj_x, ys - proj_y))

    # Score: close to corridor + high value
    candidates.sort(key=lambda s:
        _dist_to_segment(s) - 0.01 * instance._value.get(s, 0))

    # Sort by projection along A→B, then shuffle groups of similar projection
    scored = []
    for sid in candidates:
        xs, ys = instance._x[sid], instance._y[sid]
        t = max(0.0, min(1.0,
            ((xs - xa) * (xb - xa) + (ys - ya) * (yb - ya)) / max(seg_len_sq, 1e-9)))
        scored.append((t, sid))
    scored.sort(key=lambda x: x[0])

    # Target: halfway between min and max stations for a good starting point
    target_stations = (c["min_stations_per_line"] + c["max_stations_per_line"]) // 2
    target_stations = max(c["min_stations_per_line"], min(target_stations, len(scored) + 2))
    current_path = [term_a]

    # Select spaced stations with randomness for diversity
    needed = target_stations
    if len(scored) >= needed - 2:
        bucket_size = max(1, len(scored) // (needed - 2))
        for bucket_idx in range(needed - 2):
            start = bucket_idx * bucket_size
            end = min(start + bucket_size, len(scored))
            bucket = scored[start:end]
            if bucket:
                rng.shuffle(bucket)
                sid = bucket[0][1]
                if sid not in current_path:
                    current_path.append(sid)
    else:
        rng.shuffle(scored)
        for _, sid in scored:
            if len(current_path) >= needed - 1:
                break
            if sid not in current_path:
                current_path.append(sid)

    current_path.append(term_b)
    return current_path


# ---------------------------------------------------------------------------
# Mutation: only swap with nearby stations
# ---------------------------------------------------------------------------

def _mutate_nearby(
    chromosome: list[list[str]],
    instance: AhmedInstance,
    rng: np.random.Generator,
    mutation_rate: float,
) -> None:
    """Multi-operator mutation for station-level changes.

    Three operators (randomly chosen per mutated position):
      1. Swap: replace a station with a k-nearest neighbor
      2. Insert: add a new station between two existing ones
      3. Remove: delete an intermediate station
    All operators include feasibility repair.
    """
    c = instance.constraints
    for li, line in enumerate(chromosome):
        if rng.random() < mutation_rate:
            op = rng.choice(["swap", "insert", "remove"])
            if op == "swap" and len(line) >= 3:
                _mutate_swap(chromosome, li, line, instance, rng)
            elif op == "insert" and len(line) < c["max_stations_per_line"]:
                _mutate_insert(chromosome, li, line, instance, rng)
            elif op == "remove" and len(line) > c["min_stations_per_line"]:
                _mutate_remove(chromosome, li, line, instance, rng)


def _mutate_swap(
    chromosome: list[list[str]], li: int, line: list[str],
    instance: AhmedInstance, rng: np.random.Generator,
) -> None:
    """Replace one intermediate station with a nearby alternative."""
    pos = rng.integers(1, len(line) - 1)
    orig = line[pos]
    neighbors = instance._knn.get(orig, [])
    if not neighbors:
        return
    rng.shuffle(neighbors)
    for alt in neighbors[:10]:
        if alt in line:
            continue
        line[pos] = alt
        ok, _ = _check_constraints(instance, chromosome)
        if ok:
            return
        line[pos] = orig


def _mutate_insert(
    chromosome: list[list[str]], li: int, line: list[str],
    instance: AhmedInstance, rng: np.random.Generator,
) -> None:
    """Insert a new station between two consecutive stations."""
    if len(line) < 2:
        return
    # Find a long edge to break
    edges = [(instance.edge_len(line[i], line[i + 1]), i)
             for i in range(len(line) - 1)]
    edges.sort(key=lambda x: x[0], reverse=True)
    # Pick from top 3 longest edges
    top_edges = edges[:min(3, len(edges))]
    rng.shuffle(top_edges)
    for _gap, pos in top_edges:
        a, b = line[pos], line[pos + 1]
        mid_x = (instance._x[a] + instance._x[b]) / 2
        mid_y = (instance._y[a] + instance._y[b]) / 2
        candidates = [
            s for s in instance.all_ids
            if s not in line
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda s: float(np.hypot(
            instance._x[s] - mid_x, instance._y[s] - mid_y)))
        for cand in candidates[:5]:
            if instance.edge_len(a, cand) < instance.constraints["min_station_spacing_m"]:
                continue
            if instance.edge_len(cand, b) < instance.constraints["min_station_spacing_m"]:
                continue
            line.insert(pos + 1, cand)
            ok, _ = _check_constraints(instance, chromosome)
            if ok:
                return
            line.pop(pos + 1)


def _mutate_remove(
    chromosome: list[list[str]], li: int, line: list[str],
    instance: AhmedInstance, rng: np.random.Generator,
) -> None:
    """Remove one intermediate station."""
    if len(line) <= 2:
        return
    pos = rng.integers(1, len(line) - 1)
    orig = list(line)
    line.pop(pos)
    # Check spacing between the two stations that are now adjacent
    if pos > 0 and pos < len(line):
        d = instance.edge_len(line[pos - 1], line[pos])
        if d < instance.constraints["min_station_spacing_m"]:
            # Revert
            line.insert(pos, orig[pos])
            return
    ok, _ = _check_constraints(instance, chromosome)
    if not ok:
        line.insert(pos, orig[pos])


# ---------------------------------------------------------------------------
# Crossover
# ---------------------------------------------------------------------------

def _uniform_crossover(
    parent_a: list[list[str]],
    parent_b: list[list[str]],
    instance: AhmedInstance,
    rng: np.random.Generator,
) -> list[list[str]]:
    """Uniform crossover at station level with feasibility repair."""
    child = [[s for s in line] for line in parent_a]
    for li in range(len(child)):
        for pos in range(1, len(child[li]) - 1):
            if rng.random() < 0.5 and pos < len(parent_b[li]) - 1:
                orig = child[li][pos]
                child[li][pos] = parent_b[li][pos]
                ok, _ = _check_constraints(instance, child)
                if not ok:
                    child[li][pos] = orig
    return child


def _tournament_select(
    population: list,
    fitnesses: np.ndarray,
    rng: np.random.Generator,
    tournament_size: int,
) -> int:
    contenders = rng.choice(
        len(population), size=min(tournament_size, len(population)), replace=False
    )
    return int(contenders[np.argmax(fitnesses[contenders])])


# ---------------------------------------------------------------------------
# Main GA
# ---------------------------------------------------------------------------

def select_lines_ahmed_ga(
    instance: AhmedInstance,
    ga_params: dict | None = None,
    verbose: bool = True,
) -> dict:
    gp = {**DEFAULT_GA_PARAMS, **(ga_params or {})}
    rng = np.random.default_rng(gp["seed"])
    t0 = time.perf_counter()

    # Initialize with shortest-path-based chromosomes
    population = [
        _generate_chromosome_sp(instance, rng)
        for _ in range(gp["population_size"])
    ]

    best_chromosome = None
    best_fitness = float("-inf")
    history = []

    for gen in range(gp["generations"] + 1):
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
            if verbose:
                print(f"  gen {gen:3d}: best_cost={-gen_best:.1f}, mean_cost={-gen_mean:.1f}")

        if gen == gp["generations"]:
            break

        ranked = np.argsort(fitnesses)[::-1]
        next_pop_chroms = [
            [[s for s in line] for line in population[idx]]
            for idx in ranked[: gp["elite_count"]]
        ]

        while len(next_pop_chroms) < gp["population_size"]:
            pa_idx = _tournament_select(population, fitnesses, rng, gp["tournament_size"])
            pb_idx = _tournament_select(population, fitnesses, rng, gp["tournament_size"])
            if rng.random() < gp["crossover_rate"]:
                child = _uniform_crossover(
                    population[pa_idx], population[pb_idx], instance, rng
                )
            else:
                child = [[s for s in line] for line in population[pa_idx]]
            _mutate_nearby(child, instance, rng, gp["mutation_rate"])
            ok, _ = _check_constraints(instance, child)
            next_pop_chroms.append(child if ok else
                [[s for s in line] for line in population[pa_idx]])

        population = next_pop_chroms

    elapsed = time.perf_counter() - t0
    total_len = sum(
        instance.path_len(line) for line in best_chromosome
    ) if best_chromosome else 0.0
    all_stations = (
        {s for line in best_chromosome for s in line}
        if best_chromosome else set()
    )

    return {
        "chromosome": best_chromosome,
        "fitness_history": history,
        "runtime_s": elapsed,
        "final_fitness": -best_fitness,
        "n_stations_selected": len(all_stations),
        "total_length_km": total_len / 1000.0,
        "all_stations": all_stations,
        "track_graph": (
            _build_track_graph(best_chromosome, instance)
            if best_chromosome else nx.Graph()
        ),
    }


def run_multi_line_experiment(
    stations: pd.DataFrame,
    od_matrix: np.ndarray,
    corridor_graph: nx.Graph,
    n_lines: int = 1,
    ga_params: dict | None = None,
) -> dict:
    terminals = _select_terminal_pairs(stations, od_matrix, n_lines)
    instance = AhmedInstance(
        stations=stations,
        od_matrix=od_matrix,
        corridor_graph=corridor_graph,
        terminal_pairs=terminals,
    )
    return select_lines_ahmed_ga(instance, ga_params=ga_params)
