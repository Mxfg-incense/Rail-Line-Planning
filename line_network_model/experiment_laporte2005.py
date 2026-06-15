"""Experiment script for Laporte 2005 reproduction.

Runs all heuristics on Shanghai data, produces Table 1 equivalent,
spacing sensitivity analysis, runtime scalability, and PNG visualizations.

Usage:
  uv run python -m line_network_model.experiment_laporte2005
  uv run python -m line_network_model.experiment_laporte2005 --toy --lmax 60
  uv run python -m line_network_model.experiment_laporte2005 --spacing-sweep
  uv run python -m line_network_model.experiment_laporte2005 --scalability
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from line_network_model.corridor import build_corridor_mst_plus
from line_network_model.data import DEFAULT_STATION_CSV, generate_toy_stations, load_stations
from line_network_model.laporte2005 import (
    MTCPInstance,
    format_results_table,
    h1_greedy_extension,
    h2_greedy_insertion,
    post_optimize,
    run_all_heuristics,
)
from line_network_model.od import generate_od_matrix

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "laporte2005_reproduction"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def plot_alignment(
    instance: MTCPInstance,
    solution: dict,
    path: Path,
    title: str | None = None,
) -> None:
    """Plot the alignment on the corridor graph with station values shown."""
    fig, ax = plt.subplots(figsize=(11, 9))
    sid_to_xy = {}
    for row in instance.stations.itertuples(index=False):
        sid_to_xy[str(row.station_id)] = (float(row.x), float(row.y))

    # Corridor graph edges (light background)
    for u, v in instance.corridor_graph.edges():
        if u in sid_to_xy and v in sid_to_xy:
            x0, y0 = sid_to_xy[u]
            x1, y1 = sid_to_xy[v]
            ax.plot([x0, x1], [y0, y1], color="#c7ccd1", linewidth=0.5, zorder=1)

    # All stations (small gray)
    xs_all = [sid_to_xy[s][0] for s in sid_to_xy]
    ys_all = [sid_to_xy[s][1] for s in sid_to_xy]
    ax.scatter(xs_all, ys_all, s=25, color="#94a3b8", edgecolor="white",
               linewidth=0.3, zorder=2)

    # Alignment path (thick colored line)
    sol_path = solution["path"]
    if len(sol_path) >= 2:
        xs_path = [sid_to_xy[s][0] for s in sol_path]
        ys_path = [sid_to_xy[s][1] for s in sol_path]
        ax.plot(xs_path, ys_path, color="#2563eb", linewidth=4.0, alpha=0.88, zorder=3)

    # Selected stations (colored by total_value)
    values = instance.stations.set_index("station_id")["total_value"]
    max_v = values.max()
    for sid in sol_path:
        if sid in sid_to_xy:
            v = float(values.get(sid, 0.0))
            color = _value_color(v, max_v)
            r = 5.5 + 8.5 * np.sqrt(v / max(max_v, 1e-9))
            x, y = sid_to_xy[sid]
            ax.scatter([x], [y], s=r * 8, color=color, edgecolor="white",
                       linewidth=1.5, zorder=4)
            ax.annotate(sid, (x + 60, y - 40), fontsize=7, fontweight="bold",
                        color="#0f172a", zorder=5)

    method = solution["method"]
    length_km = solution["length_m"] / 1000.0
    eff = solution["effectiveness"]
    n_st = solution["n_stations"]
    ttl = title or f"{method}: {length_km:.1f} km, {eff:.1e} ridership, {n_st} stns"
    ax.set_title(ttl, fontsize=12)
    ax.set_aspect("equal", adjustable="datalim")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _value_color(value: float, max_value: float) -> str:
    ratio = min(max(value / max(max_value, 1e-9), 0.0), 1.0)
    stops = [
        (0.00, (148, 163, 184)),
        (0.35, (34, 197, 94)),
        (0.70, (37, 99, 235)),
        (1.00, (220, 38, 38)),
    ]
    for (lo_t, lo_rgb), (hi_t, hi_rgb) in zip(stops, stops[1:]):
        if ratio <= hi_t:
            blend = (ratio - lo_t) / max(hi_t - lo_t, 1e-9)
            r, g, b = (round(lo + (hi - lo) * blend) for lo, hi in zip(lo_rgb, hi_rgb))
            return f"#{r:02x}{g:02x}{b:02x}"
    return "#dc2626"


def _make_instance(stations: pd.DataFrame, lmax: float) -> MTCPInstance:
    """Create an MTCP instance from a station DataFrame."""
    od = generate_od_matrix(stations, alpha=1.5, normalize=True)
    corridor = build_corridor_mst_plus(stations, extra_edges_ratio=0.45)
    return MTCPInstance(stations=stations, od_matrix=od, corridor_graph=corridor, lmax=lmax)


def experiment_table1(stations: pd.DataFrame, lmax_values: list[float] | None = None) -> None:
    """Reproduce Table 1: all heuristics across multiple LMAX values."""
    if lmax_values is None:
        # Scale LMAX to our study area: 40, 60, 80 km
        lmax_values = [40_000.0, 60_000.0, 80_000.0]
    all_rows = []
    for lmax in lmax_values:
        instance = _make_instance(stations, lmax)
        results = run_all_heuristics(instance)
        for r in results:
            row = {
                "LMAX (km)": round(lmax / 1000.0, 1),
                "Heuristic": r["method"],
                "Length (km)": round(r["length_m"] / 1000.0, 1),
                "Ridership": round(r["effectiveness"], 1),
                "Ridership/Length": round(r["effectiveness"] / max(r["length_m"], 1.0), 4),
                "Stations": r["n_stations"],
                "Time (s)": round(r["runtime_s"], 4),
            }
            all_rows.append(row)

        # Plot best solution for each LMAX
        best = max(results, key=lambda r: r["effectiveness"])
        lmax_km = int(lmax / 1000.0)
        plot_alignment(instance, best, OUTPUT_DIR / f"alignment_lmax{lmax_km}_best.png",
                       f"Laporte 2005 — Best alignment (LMAX={lmax_km} km): {best['method']}")

        # Plot H1 vs H2-A comparison
        h1_sol = next(r for r in results if r["method"] == "H1_greedy_extension")
        plot_alignment(instance, h1_sol, OUTPUT_DIR / f"alignment_lmax{lmax_km}_h1.png")
        h2a_sol = next(r for r in results if r["method"] == "H2A_greedy_insertion")
        plot_alignment(instance, h2a_sol, OUTPUT_DIR / f"alignment_lmax{lmax_km}_h2a.png")

    df = pd.DataFrame(all_rows)
    df.to_csv(OUTPUT_DIR / "reproduction_results.csv", index=False, encoding="utf-8-sig")
    print("\n=== Table 1 Reproduction ===\n")
    print(df.to_string(index=False))


def experiment_spacing_sensitivity(stations: pd.DataFrame, lmax: float = 60_000.0) -> None:
    """Test how inter-station spacing affects H1 vs H2-A relative performance."""
    spacings = [500, 750, 1000, 1250, 1500]
    rows = []
    for min_spacing in spacings:
        od = generate_od_matrix(stations, alpha=1.5, normalize=True)
        corridor = build_corridor_mst_plus(
            stations,
            extra_edges_ratio=0.45,
            min_degree=max(2, int(6000 / min_spacing)),
        )
        # Filter out edges below min spacing
        edges_to_remove = []
        for u, v in corridor.edges():
            d = float(corridor[u][v].get("distance", 0.0))
            if d < min_spacing:
                edges_to_remove.append((u, v))
        corridor.remove_edges_from(edges_to_remove)

        instance = MTCPInstance(stations=stations, od_matrix=od, corridor_graph=corridor, lmax=lmax)
        h1 = h1_greedy_extension(instance)
        h2a = h2_greedy_insertion(instance, "A")
        h2a_po = post_optimize(instance, h2a, "A")
        for r in [h1, h2a, h2a_po]:
            rows.append({
                "MinSpacing (m)": min_spacing,
                "Heuristic": r["method"],
                "Effectiveness": round(r["effectiveness"], 1),
                "Length (km)": round(r["length_m"] / 1000.0, 1),
                "Stations": r["n_stations"],
            })

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_DIR / "spacing_sensitivity.csv", index=False, encoding="utf-8-sig")
    print("\n=== Spacing Sensitivity ===\n")
    print(df.to_string(index=False))

    # Plot
    fig, ax = plt.subplots(figsize=(9, 5))
    for method in ["H1_greedy_extension", "H2A_greedy_insertion", "H2A_greedy_insertion+postopt"]:
        sub = df[df["Heuristic"] == method]
        ax.plot(sub["MinSpacing (m)"], sub["Effectiveness"], marker="o", label=method)
    ax.set_xlabel("Minimum inter-station spacing (m)")
    ax.set_ylabel("Total effectiveness (ridership)")
    ax.set_title("Spacing Sensitivity — Laporte 2005")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "spacing_sensitivity.png", dpi=150)
    plt.close(fig)


def experiment_scalability() -> None:
    """Measure runtime vs number of candidate stations."""
    n_values = [20, 30, 40, 50, 60, 80, 100]
    rows = []
    for n in n_values:
        stations = generate_toy_stations(n)
        od = generate_od_matrix(stations, alpha=1.5, normalize=True)
        corridor = build_corridor_mst_plus(stations, extra_edges_ratio=0.4)
        lmax = float("inf")
        instance = MTCPInstance(stations=stations, od_matrix=od, corridor_graph=corridor, lmax=lmax)

        for algo_name, algo_fn in [
            ("H1", h1_greedy_extension),
            ("H2-A", lambda inst: h2_greedy_insertion(inst, "A")),
            ("H2-C", lambda inst: h2_greedy_insertion(inst, "C")),
        ]:
            t0 = time.perf_counter()
            _ = algo_fn(instance)
            elapsed = time.perf_counter() - t0
            rows.append({"n_stations": n, "Algorithm": algo_name, "Runtime (s)": elapsed})

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_DIR / "runtime_scalability.csv", index=False, encoding="utf-8-sig")
    print("\n=== Runtime Scalability ===\n")
    for n_val in n_values:
        sub = df[df["n_stations"] == n_val]
        parts = []
        for _, r in sub.iterrows():
            parts.append(f"{r['Algorithm']}={r['Runtime (s)']:.4f}s")
        print(f"  n={n_val:3d}: {', '.join(parts)}")

    # Plot
    fig, ax = plt.subplots(figsize=(9, 5))
    for algo in ["H1", "H2-A", "H2-C"]:
        sub = df[df["Algorithm"] == algo]
        ax.plot(sub["n_stations"], sub["Runtime (s)"], marker="o", label=algo)
    ax.set_xlabel("Number of candidate stations (n)")
    ax.set_ylabel("Runtime (s)")
    ax.set_title("Runtime Scalability — Laporte 2005 Heuristics")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "runtime_scalability.png", dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Laporte 2005 reproduction experiments")
    parser.add_argument("--toy", action="store_true", help="use toy 50-station dataset")
    parser.add_argument("--lmax", type=float, default=None,
                        help="single LMAX in km (default: run 40/60/80)")
    parser.add_argument("--spacing-sweep", action="store_true",
                        help="run inter-station spacing sensitivity")
    parser.add_argument("--scalability", action="store_true",
                        help="run runtime scalability experiment")
    args = parser.parse_args()

    if args.scalability:
        experiment_scalability()
        return

    stations = generate_toy_stations(50) if args.toy else load_stations(DEFAULT_STATION_CSV)

    if args.spacing_sweep:
        experiment_spacing_sensitivity(stations)
        return

    if args.lmax is not None:
        lmax_values = [args.lmax * 1000.0]
    else:
        lmax_values = None  # use defaults: 40, 60, 80 km

    experiment_table1(stations, lmax_values)

    print(f"\nOutputs written to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
