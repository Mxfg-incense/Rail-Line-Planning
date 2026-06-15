"""Experiment script for Ahmed 2020 reproduction.

Runs multi-line GA optimization on Shanghai data, produces convergence plots,
simultaneous vs individual comparison, crossover/mutation sensitivity,
and PNG visualizations.

Usage:
  uv run python -m line_network_model.experiment_ahmed2020
  uv run python -m line_network_model.experiment_ahmed2020 --lines 2
  uv run python -m line_network_model.experiment_ahmed2020 --sensitivity-crossover
  uv run python -m line_network_model.experiment_ahmed2020 --sensitivity-mutation
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from line_network_model.ahmed2020 import (
    AhmedInstance,
    DEFAULT_GA_PARAMS,
    _select_terminal_pairs,
    select_lines_ahmed_ga,
)
from line_network_model.corridor import build_corridor_mst_plus
from line_network_model.data import DEFAULT_STATION_CSV, generate_toy_stations, load_stations
from line_network_model.od import generate_od_matrix

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "ahmed2020_reproduction"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Scaled-down GA for faster experimentation
FAST_GA = {
    **DEFAULT_GA_PARAMS,
    "population_size": 80,
    "generations": 30,
}


def _make_instance(
    stations: pd.DataFrame,
    od_matrix: np.ndarray,
    corridor_graph,
    n_lines: int,
) -> AhmedInstance:
    terminals = _select_terminal_pairs(stations, od_matrix, n_lines)
    return AhmedInstance(
        stations=stations,
        od_matrix=od_matrix,
        corridor_graph=corridor_graph,
        terminal_pairs=terminals,
    )


def plot_network(
    instance: AhmedInstance,
    result: dict,
    path: Path,
    title: str,
) -> None:
    """Plot the multi-line network with station locations."""
    fig, ax = plt.subplots(figsize=(11, 9))
    sid_to_xy = {}
    for row in instance.stations.itertuples(index=False):
        sid_to_xy[str(row.station_id)] = (float(row.x), float(row.y))

    # Corridor edges
    for u, v in instance.corridor_graph.edges():
        if u in sid_to_xy and v in sid_to_xy:
            x0, y0 = sid_to_xy[u]
            x1, y1 = sid_to_xy[v]
            ax.plot([x0, x1], [y0, y1], color="#c7ccd1", linewidth=0.4, zorder=1)

    # All stations
    xs_all = [sid_to_xy[s][0] for s in sid_to_xy]
    ys_all = [sid_to_xy[s][1] for s in sid_to_xy]
    ax.scatter(xs_all, ys_all, s=20, color="#94a3b8", edgecolor="white",
               linewidth=0.3, zorder=2)

    # Selected lines with distinct colors
    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c"]
    chromosome = result.get("chromosome", [])
    for li, line_seq in enumerate(chromosome):
        c = colors[li % len(colors)]
        xs = [sid_to_xy[s][0] for s in line_seq]
        ys = [sid_to_xy[s][1] for s in line_seq]
        ax.plot(xs, ys, color=c, linewidth=3.5, alpha=0.9, zorder=3,
                label=f"Line {li+1}")

    # Terminal stations
    if chromosome:
        all_selected = {s for line in chromosome for s in line}
        for sid in all_selected:
            if sid in sid_to_xy:
                x, y = sid_to_xy[sid]
                ax.scatter([x], [y], s=60, color="#0f172a", edgecolor="white",
                           linewidth=1.5, zorder=4)
                ax.annotate(sid, (x + 60, y - 40), fontsize=6, fontweight="bold",
                            color="#0f172a", zorder=5)

    ax.set_title(title, fontsize=12)
    ax.set_aspect("equal", adjustable="datalim")
    ax.legend(fontsize=8)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def experiment_basic(stations: pd.DataFrame) -> None:
    """Run 1, 2, 3 line scenarios and produce results."""
    od = generate_od_matrix(stations, alpha=1.5, normalize=True)
    corridor = build_corridor_mst_plus(stations, extra_edges_ratio=0.45)

    rows = []
    for n_lines in [1, 2, 3]:
        print(f"\n--- {n_lines} line(s) ---")
        instance = _make_instance(stations, od, corridor, n_lines)
        result = select_lines_ahmed_ga(instance, ga_params=FAST_GA, verbose=False)

        n_st = result["n_stations_selected"]
        length = result["total_length_km"]
        cost = result["final_fitness"]
        rt = result["runtime_s"]
        print(f"  Cost: {cost:.1f}, Stations: {n_st}, Length: {length:.1f}km, Time: {rt:.1f}s")

        rows.append({
            "n_lines": n_lines,
            "total_cost": round(cost, 1),
            "n_stations": n_st,
            "total_length_km": round(length, 1),
            "runtime_s": round(rt, 1),
            "terminals": str(instance.terminal_pairs),
        })

        plot_network(
            instance, result,
            OUTPUT_DIR / f"network_{n_lines}line.png",
            f"Ahmed 2020 GA — {n_lines} line(s): {n_st} stations, {length:.1f}km",
        )

        # Convergence plot
        history = result["fitness_history"]
        if history:
            fig, ax = plt.subplots(figsize=(8, 4))
            gens = [h[0] for h in history]
            best = [h[1] for h in history]
            mean = [h[2] for h in history]
            ax.plot(gens, best, "b-", label="Best", linewidth=1.5)
            ax.plot(gens, mean, "r--", label="Mean", linewidth=1.0)
            ax.set_xlabel("Generation")
            ax.set_ylabel("Total System Cost")
            ax.set_title(f"GA Convergence — {n_lines} line(s)")
            ax.legend()
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            fig.savefig(OUTPUT_DIR / f"convergence_{n_lines}line.png", dpi=150)
            plt.close(fig)

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_DIR / "basic_results.csv", index=False, encoding="utf-8-sig")
    print("\n=== Ahmed 2020 Basic Results ===\n")
    print(df.to_string(index=False))


def experiment_simultaneous_vs_individual(stations: pd.DataFrame) -> None:
    """Compare simultaneous vs individual line optimization (Table 3 equivalent)."""
    od = generate_od_matrix(stations, alpha=1.5, normalize=True)
    corridor = build_corridor_mst_plus(stations, extra_edges_ratio=0.45)

    # Simultaneous: optimize 2 lines together
    instance_sim = _make_instance(stations, od, corridor, 2)
    t0 = time.perf_counter()
    result_sim = select_lines_ahmed_ga(instance_sim, ga_params=FAST_GA, verbose=False)
    t_sim = time.perf_counter() - t0

    # Individual: optimize each line separately
    terminals = _select_terminal_pairs(stations, od, 2)
    indiv_results = []
    t_indiv = 0.0
    for term_a, term_b in terminals:
        instance_ind = AhmedInstance(
            stations=stations, od_matrix=od, corridor_graph=corridor,
            terminal_pairs=[(term_a, term_b)],
        )
        t0 = time.perf_counter()
        r = select_lines_ahmed_ga(instance_ind, ga_params=FAST_GA, verbose=False)
        t_indiv += time.perf_counter() - t0
        indiv_results.append(r)

    indiv_cost = sum(r["final_fitness"] for r in indiv_results)
    indiv_stations = sum(r["n_stations_selected"] for r in indiv_results)
    indiv_length = sum(r["total_length_km"] for r in indiv_results)

    rows = [{
        "Method": "Simultaneous",
        "Total Cost": round(result_sim["final_fitness"], 1),
        "Stations": result_sim["n_stations_selected"],
        "Length (km)": round(result_sim["total_length_km"], 1),
        "Runtime (s)": round(t_sim, 1),
    }, {
        "Method": "Individual",
        "Total Cost": round(indiv_cost, 1),
        "Stations": indiv_stations,
        "Length (km)": round(indiv_length, 1),
        "Runtime (s)": round(t_indiv, 1),
    }]
    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_DIR / "simultaneous_vs_individual.csv", index=False, encoding="utf-8-sig")
    print("\n=== Simultaneous vs Individual ===\n")
    print(df.to_string(index=False))


def experiment_sensitivity(stations: pd.DataFrame, param: str) -> None:
    """Crossover or mutation rate sensitivity analysis."""
    od = generate_od_matrix(stations, alpha=1.5, normalize=True)
    corridor = build_corridor_mst_plus(stations, extra_edges_ratio=0.45)
    instance = _make_instance(stations, od, corridor, 1)

    if param == "crossover":
        values = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        param_key = "crossover_rate"
    else:
        values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
        param_key = "mutation_rate"

    rows = []
    for val in values:
        gp = {**FAST_GA, param_key: val}
        result = select_lines_ahmed_ga(instance, ga_params=gp, verbose=False)
        rows.append({
            param_key: val,
            "total_cost": result["final_fitness"],
        })
        print(f"  {param_key}={val}: cost={result['final_fitness']:.1f}")

    df = pd.DataFrame(rows)
    fname = f"sensitivity_{param}.csv"
    df.to_csv(OUTPUT_DIR / fname, index=False, encoding="utf-8-sig")

    # Plot
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(df[param_key], df["total_cost"], marker="o")
    ax.set_xlabel(param_key)
    ax.set_ylabel("Total System Cost")
    ax.set_title(f"Ahmed 2020 — {param} Rate Sensitivity")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / f"sensitivity_{param}.png", dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ahmed 2020 reproduction experiments")
    parser.add_argument("--toy", action="store_true")
    parser.add_argument("--lines", type=int, default=None,
                        help="run single scenario with N lines")
    parser.add_argument("--sensitivity-crossover", action="store_true")
    parser.add_argument("--sensitivity-mutation", action="store_true")
    parser.add_argument("--vs-individual", action="store_true")
    args = parser.parse_args()

    stations = generate_toy_stations(50) if args.toy else load_stations(DEFAULT_STATION_CSV)

    if args.sensitivity_crossover:
        experiment_sensitivity(stations, "crossover")
    elif args.sensitivity_mutation:
        experiment_sensitivity(stations, "mutation")
    elif args.vs_individual:
        experiment_simultaneous_vs_individual(stations)
    elif args.lines is not None:
        od = generate_od_matrix(stations, alpha=1.5, normalize=True)
        corridor = build_corridor_mst_plus(stations, extra_edges_ratio=0.45)
        instance = _make_instance(stations, od, corridor, args.lines)
        result = select_lines_ahmed_ga(instance, ga_params=FAST_GA, verbose=True)
        plot_network(instance, result,
                     OUTPUT_DIR / f"network_{args.lines}line.png",
                     f"Ahmed 2020 GA — {args.lines} line(s)")
        print(f"\nDone. Outputs: {OUTPUT_DIR}")
    else:
        experiment_basic(stations)

    print(f"\nOutputs written to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
