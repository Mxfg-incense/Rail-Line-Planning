"""Reproduction of Laporte et al. 2005 heuristics for the MTCP.

Implements:
  H1  – Greedy extension of an alignment
  H2-A – Greedy insertion (max added ridership)
  H2-B – Greedy insertion (min added length)
  H2-C – Greedy insertion (max ratio: ridership / length)
  Post-optimization – remove-and-reinsert hill-climbing

Effectiveness uses a linear-decay model (same spirit as the paper's logit):
  E(path) = sum_{i<j} v_i * v_j * max(0, 1 - D_ij / cutoff)
where D_ij = distance along the alignment path between stations i and j.
This makes order matter: stations far apart in the alignment contribute less.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd

# Default cutoff for the linear decay: same as original objective_config
DEFAULT_CUTOFF_M = 30_000.0


@dataclass
class MTCPInstance:
    """Encapsulates a Maximal Trip Coverage Problem instance."""

    stations: pd.DataFrame
    od_matrix: np.ndarray
    corridor_graph: nx.Graph
    lmax: float
    cutoff_m: float = DEFAULT_CUTOFF_M
    _id_to_idx: dict[str, int] = field(default_factory=dict)
    _idx_to_id: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ids = self.stations["station_id"].astype(str).tolist()
        self._id_to_idx = {sid: i for i, sid in enumerate(ids)}
        self._idx_to_id = {i: sid for i, sid in enumerate(ids)}
        xy_df = self.stations.set_index("station_id")
        self._x = {sid: float(xy_df.loc[sid, "x"]) for sid in ids}
        self._y = {sid: float(xy_df.loc[sid, "y"]) for sid in ids}
        self._value = {
            sid: float(xy_df.loc[sid, "total_value"]) for sid in ids
        }

    def station_value(self, sid: str) -> float:
        return self._value.get(sid, 0.0)

    def od(self, u: str, v: str) -> float:
        i, j = self._id_to_idx[u], self._id_to_idx[v]
        return float(self.od_matrix[i, j] + self.od_matrix[j, i])

    def edge_len(self, u: str, v: str) -> float:
        if self.corridor_graph.has_edge(u, v):
            return float(self.corridor_graph[u][v].get("distance", 0.0))
        return float(np.hypot(self._x[u] - self._x[v], self._y[u] - self._y[v]))

    def path_length(self, path: list[str]) -> float:
        return sum(self.edge_len(path[i], path[i + 1]) for i in range(len(path) - 1))

    def _cumulative(self, path: list[str]) -> np.ndarray:
        """Cumulative distances along the path (length = len(path))."""
        cumul = np.zeros(len(path), dtype=float)
        for i in range(1, len(path)):
            cumul[i] = cumul[i - 1] + self.edge_len(path[i - 1], path[i])
        return cumul

    def effectiveness(self, path: list[str], cutoff: float | None = None) -> float:
        """Total pair reward with linear decay along the alignment.

        E = sum_{i<j} v_i * v_j * max(0, 1 - D_ij / cutoff)
        where D_ij = |cumul[j] - cumul[i]| along the ordered path.
        """
        cutoff = cutoff if cutoff is not None else self.cutoff_m
        n = len(path)
        if n < 2:
            return 0.0
        cumul = self._cumulative(path)
        values = np.array([self.station_value(s) for s in path], dtype=float)
        i_idx, j_idx = np.triu_indices(n, k=1)
        d = cumul[j_idx] - cumul[i_idx]
        decay = np.maximum(0.0, 1.0 - d / cutoff)
        return float(np.sum(values[i_idx] * values[j_idx] * decay))


def _best_starting_edge(instance: MTCPInstance) -> list[str]:
    """Find the station pair with max effectiveness that fits within LMAX."""
    best = None
    best_val = -1.0
    ids = instance.stations["station_id"].astype(str).tolist()
    for u, v in itertools.combinations(ids, 2):
        d = instance.edge_len(u, v)
        if d > instance.lmax:
            continue
        # For a 2-station path: E = v_u * v_v * max(0, 1 - d/cutoff)
        decay = max(0.0, 1.0 - d / instance.cutoff_m)
        val = instance.station_value(u) * instance.station_value(v) * decay
        if val > best_val:
            best_val = val
            best = [u, v]
    if best is None:
        raise ValueError(f"No feasible starting edge within LMAX={instance.lmax}")
    return best


def h1_greedy_extension(instance: MTCPInstance) -> dict:
    """H1: Greedy extension of an alignment (Laporte 2005, Section 3).

    Start from the best edge and repeatedly extend at either endpoint
    with the station yielding the largest gain in effectiveness.
    Computes full effectiveness from scratch with numpy (fast at our scale).
    """
    t0 = time.perf_counter()
    path = _best_starting_edge(instance)
    current_len = instance.path_length(path)
    current_eff = instance.effectiveness(path)
    unselected = set(instance.stations["station_id"].astype(str)) - set(path)

    while unselected:
        best_node = None
        best_prepend = False
        best_gain = 0.0
        for node in unselected:
            # Try prepend
            added = instance.edge_len(node, path[0])
            if current_len + added <= instance.lmax:
                candidate = [node] + path
                gain = instance.effectiveness(candidate) - current_eff
                if gain > best_gain:
                    best_gain = gain
                    best_node = node
                    best_prepend = True
            # Try append
            added = instance.edge_len(path[-1], node)
            if current_len + added <= instance.lmax:
                candidate = path + [node]
                gain = instance.effectiveness(candidate) - current_eff
                if gain > best_gain:
                    best_gain = gain
                    best_node = node
                    best_prepend = False
        if best_node is None:
            break
        if best_prepend:
            current_len += instance.edge_len(best_node, path[0])
            path = [best_node] + path
        else:
            current_len += instance.edge_len(path[-1], best_node)
            path = path + [best_node]
        unselected.remove(best_node)
        current_eff += best_gain

    elapsed = time.perf_counter() - t0
    return {
        "method": "H1_greedy_extension",
        "path": path,
        "length_m": current_len,
        "effectiveness": current_eff,
        "n_stations": len(path),
        "runtime_s": elapsed,
    }


def h2_greedy_insertion(instance: MTCPInstance, variant: str = "A") -> dict:
    """H2: Greedy insertion heuristics (Laporte 2005, Section 3).

    Variants:
      'A' – insert station with max added ridership
      'B' – insert station with min added length
      'C' – insert station with max ratio (ridership / length)

    For each unselected node, finds the best insertion position, then picks
    the node that maximizes the variant's criterion.
    """
    t0 = time.perf_counter()
    path = _best_starting_edge(instance)
    current_len = instance.path_length(path)
    current_eff = instance.effectiveness(path)
    unselected = set(instance.stations["station_id"].astype(str)) - set(path)

    while unselected:
        best_info = None  # (node, pos, gain, added_len, key)
        for node in unselected:
            best_for_node = None  # (pos, gain, added_len, key)
            # Try all insertion positions
            for pos in range(len(path) + 1):
                candidate = path[:pos] + [node] + path[pos:]
                cand_len = instance.path_length(candidate)
                if cand_len > instance.lmax:
                    continue
                gain = instance.effectiveness(candidate) - current_eff
                added_len = cand_len - current_len
                if variant == "A":
                    key = gain
                elif variant == "B":
                    key = -added_len
                else:
                    key = gain / max(added_len, 1e-9)
                if best_for_node is None or key > best_for_node[3]:
                    best_for_node = (pos, gain, added_len, key)
            if best_for_node is None:
                continue
            pos, gain, added_len, key = best_for_node
            if best_info is None or key > best_info[4]:
                best_info = (node, pos, gain, added_len, key)

        if best_info is None:
            break
        node, pos, gain, added_len, _key = best_info
        path = path[:pos] + [node] + path[pos:]
        unselected.remove(node)
        current_len += added_len
        current_eff += gain

    elapsed = time.perf_counter() - t0
    return {
        "method": f"H2{variant}_greedy_insertion",
        "path": path,
        "length_m": current_len,
        "effectiveness": current_eff,
        "n_stations": len(path),
        "runtime_s": elapsed,
    }


def post_optimize(
    instance: MTCPInstance, solution: dict, variant: str = "A"
) -> dict:
    """Post-optimization: remove and reinsert each station (Laporte 2005, Sec 3.1).

    For each station, removes it and tries to reinsert at the best position.
    Only accepts if the new effectiveness strictly exceeds the original.
    Terminates when a full pass yields no improvement.
    """
    t0 = time.perf_counter()
    path = list(solution["path"])
    current_len = solution["length_m"]
    current_eff = solution["effectiveness"]
    iterations = 0

    improved = True
    while improved:
        improved = False
        for i in range(len(path)):
            orig_path = list(path)
            orig_len = current_len
            orig_eff = current_eff

            node = path.pop(i)
            # Recompute state after removal
            current_len = instance.path_length(path)
            current_eff = instance.effectiveness(path)

            # Find best position to reinsert
            best_for_node = None
            for pos in range(len(path) + 1):
                candidate = path[:pos] + [node] + path[pos:]
                cand_len = instance.path_length(candidate)
                if cand_len > instance.lmax:
                    continue
                cand_eff = instance.effectiveness(candidate)
                gain = cand_eff - current_eff
                added_len = cand_len - current_len
                if variant == "A":
                    key = gain
                elif variant == "B":
                    key = -added_len
                else:
                    key = gain / max(added_len, 1e-9)
                if best_for_node is None or key > best_for_node[2]:
                    best_for_node = (pos, cand_eff, key, cand_len, gain, added_len)

            if best_for_node is None:
                path = orig_path
                current_len = orig_len
                current_eff = orig_eff
                continue

            pos, cand_eff, _key, cand_len, _gain, _added_len = best_for_node
            if cand_eff > orig_eff + 1e-9:
                path = path[:pos] + [node] + path[pos:]
                current_len = cand_len
                current_eff = cand_eff
                improved = True
                iterations += 1
            else:
                path = orig_path
                current_len = orig_len
                current_eff = orig_eff

    elapsed = time.perf_counter() - t0
    return {
        "method": f"{solution['method']}+postopt",
        "path": path,
        "length_m": current_len,
        "effectiveness": current_eff,
        "n_stations": len(path),
        "runtime_s": elapsed,
        "postopt_iterations": iterations,
    }


def run_all_heuristics(instance: MTCPInstance) -> list[dict]:
    """Run H1, H2-A, H2-B, H2-C, and H2-A+postopt on the given instance."""
    results = []
    results.append(h1_greedy_extension(instance))
    for variant in ("A", "B", "C"):
        h2 = h2_greedy_insertion(instance, variant)
        results.append(h2)
        if variant == "A":
            results.append(post_optimize(instance, h2, variant))
    return results


def format_results_table(results: list[dict]) -> pd.DataFrame:
    rows = []
    for r in results:
        ratio = r["effectiveness"] / max(r["length_m"], 1.0)
        rows.append({
            "Heuristic": r["method"],
            "Length (km)": round(r["length_m"] / 1000.0, 1),
            "Ridership": round(r["effectiveness"], 1),
            "Ridership/Length": round(ratio, 4),
            "Stations": r["n_stations"],
            "Time (s)": round(r["runtime_s"], 4),
        })
    return pd.DataFrame(rows)
