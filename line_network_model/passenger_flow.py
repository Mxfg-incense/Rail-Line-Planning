"""Passenger flow analysis for selected rail networks.

Computes section-level passenger volumes via all-or-nothing assignment:
  - For each OD pair, find the shortest path on the track network
  - Accumulate OD demand onto each edge (section flow)
  - Report maximum section flow and flow distribution

Also computes:
  - Flow intensity heatmap (edges colored by passenger volume)
  - Maximum section passenger flow (capacity planning metric)
  - Flow-weighted network utilization statistics
"""

from __future__ import annotations

import itertools
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd

from line_network_model.corridor import build_corridor_mst_plus
from line_network_model.data import DEFAULT_STATION_CSV, load_stations
from line_network_model.od import generate_od_matrix

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "passenger_flow"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def assign_flows(
    track_graph: nx.Graph,
    stations: pd.DataFrame,
    od_matrix: np.ndarray,
    covered_stations: set[str],
) -> dict[tuple[str, str], float]:
    """All-or-nothing assignment: route every OD pair via shortest path.

    Returns: dict mapping (u, v) -> total passenger flow on that edge.
    """
    id_to_idx = {sid: i for i, sid in enumerate(stations["station_id"].astype(str))}
    ids = stations["station_id"].astype(str).tolist()
    n = len(ids)

    # Find nearest covered station for each demand point
    nearest = {}
    for sid in ids:
        best_s = None
        best_d = float("inf")
        for cs in covered_stations:
            if cs not in track_graph:
                continue
            if track_graph.has_edge(sid, cs) or sid == cs:
                d = 0.0 if sid == cs else track_graph[sid][cs].get("distance", float("inf"))
                if d < best_d:
                    best_d = d
                    best_s = cs
        if best_s is not None:
            nearest[sid] = best_s

    flow: dict[tuple[str, str], float] = {}
    for u, v in track_graph.edges():
        flow[(u, v)] = 0.0
        flow[(v, u)] = 0.0

    # Route each OD pair
    for i in range(n):
        for j in range(i + 1, n):
            u, v = ids[i], ids[j]
            od_val = float(od_matrix[i, j] + od_matrix[j, i])
            if od_val <= 0:
                continue
            nu, nv = nearest.get(u), nearest.get(v)
            if nu is None or nv is None or nu == nv:
                continue
            try:
                path = nx.shortest_path(track_graph, nu, nv, weight="distance")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            # Accumulate flow on each edge along the path
            for a, b in zip(path[:-1], path[1:]):
                key = (a, b) if (a, b) in flow else (b, a)
                if key in flow:
                    flow[key] += od_val

    return flow


def compute_flow_metrics(
    flow: dict[tuple[str, str], float],
) -> dict:
    """Compute flow statistics from edge flows."""
    flows = list(flow.values())
    if not flows:
        return {}
    arr = np.array(flows)
    total = float(arr.sum())
    return {
        "max_section_flow": float(arr.max()),
        "mean_section_flow": float(arr.mean()),
        "median_section_flow": float(np.median(arr)),
        "std_section_flow": float(arr.std()),
        "total_network_flow": total,
        "n_edges_with_flow": int((arr > 0).sum()),
        "n_edges_total": len(flows),
        "flow_concentration": float(
            np.sort(arr)[-5:].sum() / max(total, 1e-9)
        ),  # top-5 edges share of total flow
    }


def plot_flow_network(
    track_graph: nx.Graph,
    flow: dict[tuple[str, str], float],
    stations: pd.DataFrame,
    title: str,
    path: Path,
) -> None:
    """Plot the network with edges colored by passenger flow intensity."""
    fig, ax = plt.subplots(figsize=(11, 9))

    sid_to_xy = {}
    for row in stations.itertuples(index=False):
        sid_to_xy[str(row.station_id)] = (float(row.x), float(row.y))

    max_flow = max(flow.values()) if flow else 1.0

    # Draw edges with flow-based coloring
    for (u, v), f in flow.items():
        if u not in sid_to_xy or v not in sid_to_xy:
            continue
        x0, y0 = sid_to_xy[u]
        x1, y1 = sid_to_xy[v]
        ratio = min(f / max(max_flow, 1e-9), 1.0)
        # Color: gray (no flow) -> blue (medium) -> red (high)
        if ratio < 0.01:
            color = "#e2e8f0"
            lw = 0.5
        elif ratio < 0.3:
            color = "#60a5fa"
            lw = 1.5 + 4.0 * ratio
        elif ratio < 0.7:
            color = "#f59e0b"
            lw = 3.0 + 3.0 * ratio
        else:
            color = "#dc2626"
            lw = 4.0 + 2.0 * ratio
        ax.plot([x0, x1], [y0, y1], color=color, linewidth=lw, alpha=0.85, zorder=3)

    # Draw stations
    xs_all = [sid_to_xy[s][0] for s in sid_to_xy if s in sid_to_xy]
    ys_all = [sid_to_xy[s][1] for s in sid_to_xy if s in sid_to_xy]
    ax.scatter(xs_all, ys_all, s=20, color="#94a3b8", edgecolor="white",
               linewidth=0.3, zorder=2)

    # Selected stations
    selected = set()
    for u, v in track_graph.edges():
        selected.add(u)
        selected.add(v)
    for sid in selected:
        if sid in sid_to_xy:
            x, y = sid_to_xy[sid]
            ax.scatter([x], [y], s=40, color="#0f172a", edgecolor="white",
                       linewidth=1, zorder=4)

    ax.set_title(title, fontsize=12)
    ax.set_aspect("equal", adjustable="datalim")
    ax.axis("off")

    # Legend
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color="#e2e8f0", linewidth=1, label="Low flow"),
        Line2D([0], [0], color="#60a5fa", linewidth=2.5, label="Medium flow"),
        Line2D([0], [0], color="#f59e0b", linewidth=3, label="High flow"),
        Line2D([0], [0], color="#dc2626", linewidth=3.5, label="Peak flow"),
    ]
    ax.legend(handles=legend_elements, fontsize=7, loc="lower right")

    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def run_passenger_flow_analysis(
    method_name: str,
    track_graph: nx.Graph,
    stations: pd.DataFrame,
    od_matrix: np.ndarray,
    covered_stations: set[str],
) -> dict:
    """Run full passenger flow analysis for one method's network."""
    flow = assign_flows(track_graph, stations, od_matrix, covered_stations)
    metrics = compute_flow_metrics(flow)

    safe_name = method_name.replace(" ", "_").replace("(", "").replace(")", "")
    plot_flow_network(
        track_graph, flow, stations,
        f"{method_name} — Max section flow: {metrics.get('max_section_flow', 0):.1e}",
        OUTPUT_DIR / f"flow_{safe_name}.png",
    )

    return {
        "method": method_name,
        **metrics,
    }


def main() -> None:
    from line_network_model.ahmed2020 import (
        AhmedInstance, _select_terminal_pairs, select_lines_ahmed_ga,
        DEFAULT_GA_PARAMS,
    )
    from line_network_model.laporte2005 import (
        MTCPInstance, h1_greedy_extension, h2_greedy_insertion,
    )

    stations = load_stations(DEFAULT_STATION_CSV)
    od = generate_od_matrix(stations, alpha=1.5, normalize=True)
    corridor = build_corridor_mst_plus(stations, extra_edges_ratio=0.45)

    rows = []
    print("=== Passenger Flow Analysis ===\n")

    # --- Laporte H1 ---
    instance = MTCPInstance(stations=stations, od_matrix=od,
                            corridor_graph=corridor, lmax=60000.0)
    result = h1_greedy_extension(instance)
    tg = nx.Graph()
    path = result["path"]
    for u, v in zip(path[:-1], path[1:]):
        d = instance.edge_len(u, v)
        tg.add_edge(u, v, distance=d)
    rows.append(run_passenger_flow_analysis(
        "Laporte-H1", tg, stations, od, set(path)))

    # --- Laporte H2-A ---
    result = h2_greedy_insertion(instance, "A")
    tg = nx.Graph()
    for u, v in zip(result["path"][:-1], result["path"][1:]):
        d = instance.edge_len(u, v)
        tg.add_edge(u, v, distance=d)
    rows.append(run_passenger_flow_analysis(
        "Laporte-H2A", tg, stations, od, set(result["path"])))

    # --- Ahmed 2-line ---
    ga_params = {**DEFAULT_GA_PARAMS, "population_size": 80, "generations": 30}
    terminals = _select_terminal_pairs(stations, od, 2)
    ahmed_inst = AhmedInstance(
        stations=stations, od_matrix=od, corridor_graph=corridor,
        terminal_pairs=terminals,
    )
    ahmed_result = select_lines_ahmed_ga(ahmed_inst, ga_params=ga_params, verbose=False)
    tg_ahmed = ahmed_result["track_graph"]
    rows.append(run_passenger_flow_analysis(
        "Ahmed-GA-2L", tg_ahmed, stations, od, ahmed_result["all_stations"]))

    # --- Ahmed 3-line ---
    terminals = _select_terminal_pairs(stations, od, 3)
    ahmed_inst = AhmedInstance(
        stations=stations, od_matrix=od, corridor_graph=corridor,
        terminal_pairs=terminals,
    )
    ahmed_result = select_lines_ahmed_ga(ahmed_inst, ga_params=ga_params, verbose=False)
    tg_ahmed3 = ahmed_result["track_graph"]
    rows.append(run_passenger_flow_analysis(
        "Ahmed-GA-3L", tg_ahmed3, stations, od, ahmed_result["all_stations"]))

    # --- Summary table ---
    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_DIR / "passenger_flow_summary.csv",
              index=False, encoding="utf-8-sig")

    print("\n=== Section Flow Summary ===\n")
    for _, r in df.iterrows():
        print(f"  {r['method']}: max_flow={r['max_section_flow']:.1e}, "
              f"mean={r['mean_section_flow']:.1e}, "
              f"top5_share={r['flow_concentration']:.1%}, "
              f"edges_w_flow={int(r['n_edges_with_flow'])}/{int(r['n_edges_total'])}")

    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
