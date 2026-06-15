"""Runnable candidate-line-pool + line-selection experiment."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from line_network_model.corridor import build_corridor_mst_plus
from line_network_model.data import DEFAULT_STATION_CSV, generate_toy_stations, load_stations
from line_network_model.evaluation import evaluate_network
from line_network_model.line_pool import generate_candidate_line_pool
from line_network_model.od import generate_od_matrix
from line_network_model.selection import select_lines_ga, select_lines_greedy_bcr, select_lines_greedy_od, select_lines_ilp


OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "candidate_line_pool_experiment"


def plot_network(stations: pd.DataFrame, corridor_graph, selected_lines: list[dict], path: Path, title: str) -> None:
    """Plot stations, candidate corridors, and selected lines."""
    fig, ax = plt.subplots(figsize=(9, 8))
    pos = {str(r.station_id): (float(r.x), float(r.y)) for r in stations.itertuples(index=False)}
    for u, v in corridor_graph.edges:
        x0, y0 = pos[u]
        x1, y1 = pos[v]
        ax.plot([x0, x1], [y0, y1], color="#c7ccd1", linewidth=0.7, zorder=1)
    colors = plt.cm.tab10.colors
    for i, line in enumerate(selected_lines):
        seq = line["station_sequence"]
        xs = [pos[s][0] for s in seq]
        ys = [pos[s][1] for s in seq]
        ax.plot(xs, ys, color=colors[i % len(colors)], linewidth=2.8, alpha=0.9, zorder=3)
    sizes = 20 + 90 * stations["total_value"] / max(stations["total_value"].max(), 1e-9)
    ax.scatter(stations["x"], stations["y"], s=sizes, color="#243447", edgecolor="white", linewidth=0.6, zorder=4)
    ax.set_title(title)
    ax.set_aspect("equal", adjustable="datalim")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def run_experiment(
    station_path: str | Path | None = DEFAULT_STATION_CSV,
    use_toy: bool = False,
    max_lines: int = 8,
    budget: float | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> pd.DataFrame:
    """Run the full experiment and return a metrics table."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stations = generate_toy_stations(50) if use_toy else load_stations(station_path or DEFAULT_STATION_CSV)
    od_matrix = generate_od_matrix(stations, alpha=1.5, normalize=True)
    corridor_graph = build_corridor_mst_plus(stations, extra_edges_ratio=0.45, cost_per_km=1.0)
    line_pool = generate_candidate_line_pool(stations, corridor_graph, od_matrix)

    pd.DataFrame(line_pool).to_csv(output_dir / "candidate_line_pool.csv", index=False, encoding="utf-8-sig")
    methods = {
        "greedy_od": select_lines_greedy_od,
        "greedy_bcr": select_lines_greedy_bcr,
        "ga": lambda pool, od, max_lines=None, budget=None: select_lines_ga(
            pool, od, max_lines=max_lines, budget=budget, population_size=80, generations=120
        ),
        "ilp": select_lines_ilp,
    }

    rows = []
    for name, method in methods.items():
        selected = method(line_pool, od_matrix, max_lines=max_lines, budget=budget)
        metrics = evaluate_network(selected, stations, od_matrix, corridor_graph)
        metrics["method"] = name
        metrics["selected_line_ids"] = ",".join(line["line_id"] for line in selected)
        rows.append(metrics)
        pd.DataFrame(selected).to_csv(output_dir / f"selected_lines_{name}.csv", index=False, encoding="utf-8-sig")
        plot_network(stations, corridor_graph, selected, output_dir / f"network_{name}.png", f"Selected network: {name}")

    table = pd.DataFrame(rows).set_index("method")
    table.to_csv(output_dir / "evaluation_results.csv", encoding="utf-8-sig")
    plot_network(stations, corridor_graph, [], output_dir / "candidate_corridors.png", "Candidate corridors")
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", type=Path, default=DEFAULT_STATION_CSV, help="station CSV path")
    parser.add_argument("--toy", action="store_true", help="use a generated 50-station toy dataset")
    parser.add_argument("--max-lines", type=int, default=8)
    parser.add_argument("--budget", type=float, default=None)
    args = parser.parse_args()
    table = run_experiment(args.stations, use_toy=args.toy, max_lines=args.max_lines, budget=args.budget)
    cols = [
        "num_lines",
        "unique_track_length",
        "population_coverage_ratio",
        "direct_od_coverage_ratio",
        "network_od_coverage_ratio",
        "weighted_avg_detour_ratio",
        "weighted_avg_transfer_count",
        "largest_component_station_ratio",
    ]
    print(table[cols].round(4).to_string())
    print(f"\nOutputs written to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

