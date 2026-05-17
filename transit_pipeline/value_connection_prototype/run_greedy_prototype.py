#!/usr/bin/env python3
"""Random value-connection prototype with greedy and exact baselines.

The prototype matches the simplified complete-graph proposal model:

    value(i, j) = v_i * v_j / dist(i, j)
    cost(i, j) = Euclidean distance

Mandatory nodes have zero reward but must be connected. The greedy baseline
starts from mandatory nodes, then repeatedly attaches the demand node with the
largest marginal pair value per added connection cost.

For small instances, the exact baseline enumerates all demand-node subsets and
uses the complete-graph MST cost of each subset plus mandatory nodes. This gives
the optimal connected-network solution for this simplified prototype model.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib.pyplot as plt


HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"


@dataclass(frozen=True)
class Node:
    node_id: str
    x: float
    y: float
    value: float
    mandatory: bool


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    cost: float
    reason: str


def distance(a: Node, b: Node) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def generate_nodes(
    rng: random.Random,
    demand_nodes: int,
    mandatory_nodes: int,
    width: float,
    height: float,
) -> list[Node]:
    nodes: list[Node] = []

    # Clustered demand points make the output look more like a city than pure
    # uniform noise, while still keeping the experiment synthetic.
    centers = [
        (0.25 * width, 0.30 * height),
        (0.70 * width, 0.35 * height),
        (0.48 * width, 0.72 * height),
    ]
    for idx in range(demand_nodes):
        cx, cy = rng.choice(centers)
        x = min(width, max(0.0, rng.gauss(cx, 0.13 * width)))
        y = min(height, max(0.0, rng.gauss(cy, 0.13 * height)))
        value = rng.randint(8, 100)
        nodes.append(Node(f"V{idx + 1:02d}", x, y, float(value), False))

    for idx in range(mandatory_nodes):
        x = rng.uniform(0.10 * width, 0.90 * width)
        y = rng.uniform(0.10 * height, 0.90 * height)
        nodes.append(Node(f"K{idx + 1:02d}", x, y, 0.0, True))

    return nodes


def node_by_id(nodes: list[Node]) -> dict[str, Node]:
    return {node.node_id: node for node in nodes}


def cheapest_attachment(candidate: Node, selected: set[str], nodes: dict[str, Node]) -> tuple[str, float]:
    best_parent = min(selected, key=lambda node_id: distance(candidate, nodes[node_id]))
    return best_parent, distance(candidate, nodes[best_parent])


def connect_mandatory_nodes(mandatory: list[Node]) -> tuple[set[str], list[Edge], float]:
    """Connect mandatory nodes with a Prim-style MST over the complete graph."""
    if not mandatory:
        return set(), [], 0.0

    selected = {mandatory[0].node_id}
    remaining = {node.node_id for node in mandatory[1:]}
    mandatory_by_id = node_by_id(mandatory)
    edges: list[Edge] = []
    total_cost = 0.0

    while remaining:
        best: tuple[str, str, float] | None = None
        for source in selected:
            for target in remaining:
                cost = distance(mandatory_by_id[source], mandatory_by_id[target])
                if best is None or cost < best[2]:
                    best = (source, target, cost)
        assert best is not None
        source, target, cost = best
        selected.add(target)
        remaining.remove(target)
        total_cost += cost
        edges.append(Edge(source, target, cost, "mandatory_mst"))

    return selected, edges, total_cost


def mst_for_node_ids(node_ids: set[str], nodes: dict[str, Node], reason: str) -> tuple[list[Edge], float]:
    """Return the MST over selected node ids in the complete Euclidean graph."""
    if len(node_ids) <= 1:
        return [], 0.0

    selected = {min(node_ids)}
    remaining = set(node_ids) - selected
    edges: list[Edge] = []
    total_cost = 0.0

    while remaining:
        best: tuple[str, str, float] | None = None
        for source in selected:
            for target in remaining:
                cost = distance(nodes[source], nodes[target])
                if best is None or cost < best[2]:
                    best = (source, target, cost)
        assert best is not None
        source, target, cost = best
        selected.add(target)
        remaining.remove(target)
        edges.append(Edge(source, target, cost, reason))
        total_cost += cost

    return edges, total_cost


def captured_pair_value(selected: set[str], nodes: dict[str, Node]) -> float:
    total = 0.0
    demand_nodes = [nodes[node_id] for node_id in selected if not nodes[node_id].mandatory]
    for idx, node_i in enumerate(demand_nodes):
        for node_j in demand_nodes[idx + 1 :]:
            total += pair_connection_value(node_i, node_j)
    return total


def pair_connection_value(a: Node, b: Node) -> float:
    return (a.value * b.value) / max(distance(a, b), 1e-9)


def pair_value_from_ids(node_ids: set[str], nodes: dict[str, Node]) -> float:
    return captured_pair_value(node_ids, nodes)


def exact_connect(nodes: list[Node], budget: float) -> tuple[set[str], list[Edge], dict]:
    """Brute-force the optimal demand subset under the MST budget.

    The exact search enumerates every subset of demand nodes. For each subset,
    it forces all mandatory nodes into the solution, connects the resulting node
    set by an MST in the complete Euclidean graph, and keeps the feasible subset
    with maximum pair value.
    """
    nodes_map = node_by_id(nodes)
    demand_ids = [node.node_id for node in nodes if not node.mandatory]
    mandatory_ids = {node.node_id for node in nodes if node.mandatory}

    best_selected: set[str] | None = None
    best_edges: list[Edge] = []
    best_cost = float("inf")
    best_value = -1.0
    checked_subsets = 0
    feasible_subsets = 0

    for mask in range(1 << len(demand_ids)):
        checked_subsets += 1
        selected = set(mandatory_ids)
        for idx, node_id in enumerate(demand_ids):
            if mask & (1 << idx):
                selected.add(node_id)

        edges, cost = mst_for_node_ids(selected, nodes_map, "exact_mst")
        if cost > budget:
            continue

        feasible_subsets += 1
        value = pair_value_from_ids(selected, nodes_map)
        demand_count = sum(1 for node_id in selected if not nodes_map[node_id].mandatory)
        best_demand_count = (
            -1
            if best_selected is None
            else sum(1 for node_id in best_selected if not nodes_map[node_id].mandatory)
        )
        is_better = (
            value > best_value
            or (math.isclose(value, best_value) and cost < best_cost)
            or (
                math.isclose(value, best_value)
                and math.isclose(cost, best_cost)
                and demand_count > best_demand_count
            )
        )
        if is_better:
            best_selected = selected
            best_edges = edges
            best_cost = cost
            best_value = value

    if best_selected is None:
        best_selected = set(mandatory_ids)
        best_edges, best_cost = mst_for_node_ids(best_selected, nodes_map, "exact_mst_over_budget")
        best_value = pair_value_from_ids(best_selected, nodes_map)

    stats = {
        "checked_subsets": checked_subsets,
        "feasible_subsets": feasible_subsets,
        "optimal_cost": round(best_cost, 3),
        "optimal_pair_value": round(best_value, 3),
    }
    return best_selected, best_edges, stats


def greedy_connect(nodes: list[Node], budget: float) -> tuple[set[str], list[Edge], list[dict]]:
    nodes_map = node_by_id(nodes)
    demand = [node for node in nodes if not node.mandatory]
    mandatory = [node for node in nodes if node.mandatory]

    selected, edges, spent = connect_mandatory_nodes(mandatory)
    trace: list[dict] = [
        {
            "step": 0,
            "action": "connect_mandatory_nodes",
            "spent": round(spent, 3),
            "remaining_budget": round(budget - spent, 3),
            "selected": sorted(selected),
        }
    ]

    if spent > budget:
        return selected, edges, trace

    if not selected:
        seed = max(demand, key=lambda node: node.value)
        selected.add(seed.node_id)
        trace.append(
            {
                "step": 1,
                "action": "seed_highest_value",
                "node": seed.node_id,
                "node_value": seed.value,
                "spent": round(spent, 3),
                "remaining_budget": round(budget - spent, 3),
            }
        )

    step = len(trace)
    while True:
        selected_demand_value = sum(nodes_map[node_id].value for node_id in selected if not nodes_map[node_id].mandatory)
        best: dict | None = None

        for candidate in demand:
            if candidate.node_id in selected:
                continue
            parent, added_cost = cheapest_attachment(candidate, selected, nodes_map)
            if spent + added_cost > budget:
                continue

            # Complete-graph pair value follows a gravity-style form:
            # v_i * v_j / dist(i, j). The first demand node alone creates no
            # pair value, so its own value is used only to seed the network.
            marginal_value = candidate.value if selected_demand_value <= 0 else sum(
                pair_connection_value(candidate, nodes_map[node_id])
                for node_id in selected
                if not nodes_map[node_id].mandatory
            )
            ratio = marginal_value / max(added_cost, 1e-9)
            option = {
                "candidate": candidate.node_id,
                "parent": parent,
                "added_cost": added_cost,
                "marginal_value": marginal_value,
                "ratio": ratio,
            }
            if best is None or option["ratio"] > best["ratio"]:
                best = option

        if best is None:
            break

        candidate_id = str(best["candidate"])
        parent_id = str(best["parent"])
        added_cost = float(best["added_cost"])
        selected.add(candidate_id)
        spent += added_cost
        edges.append(Edge(parent_id, candidate_id, added_cost, "greedy_value_per_cost"))
        trace.append(
            {
                "step": step,
                "action": "attach_demand_node",
                "node": candidate_id,
                "parent": parent_id,
                "added_cost": round(added_cost, 3),
                "marginal_value": round(float(best["marginal_value"]), 3),
                "value_per_cost": round(float(best["ratio"]), 3),
                "spent": round(spent, 3),
                "remaining_budget": round(budget - spent, 3),
            }
        )
        step += 1

    return selected, edges, trace


def write_nodes_csv(nodes: list[Node], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["node_id", "x", "y", "value", "mandatory"])
        writer.writeheader()
        for node in nodes:
            row = asdict(node)
            row["x"] = round(node.x, 3)
            row["y"] = round(node.y, 3)
            row["value"] = round(node.value, 3)
            writer.writerow(row)


def write_edges_csv(edges: list[Edge], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "target", "cost", "reason"])
        writer.writeheader()
        for edge in edges:
            row = asdict(edge)
            row["cost"] = round(edge.cost, 3)
            writer.writerow(row)


def plot_solution(
    nodes: list[Node],
    selected: set[str],
    edges: list[Edge],
    budget: float,
    output_file: Path,
    title: str,
) -> None:
    nodes_map = node_by_id(nodes)
    max_value = max(node.value for node in nodes if not node.mandatory)

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.set_facecolor("#f8fafc")

    # Faint nearest-neighbor links communicate that distance is the edge cost
    # without visually overwhelming the selected network.
    for node in nodes:
        neighbors = sorted(
            (other for other in nodes if other.node_id != node.node_id),
            key=lambda other: distance(node, other),
        )[:3]
        for other in neighbors:
            ax.plot([node.x, other.x], [node.y, other.y], color="#d8dee9", linewidth=0.55, zorder=1)

    for edge in edges:
        source = nodes_map[edge.source]
        target = nodes_map[edge.target]
        ax.plot([source.x, target.x], [source.y, target.y], color="#2563eb", linewidth=2.8, zorder=3)
        mid_x = (source.x + target.x) / 2
        mid_y = (source.y + target.y) / 2
        ax.text(
            mid_x,
            mid_y,
            f"{edge.cost:.0f}",
            fontsize=7,
            color="#1e3a8a",
            ha="center",
            va="center",
            bbox={"boxstyle": "round,pad=0.14", "facecolor": "white", "edgecolor": "#bfdbfe", "alpha": 0.85},
            zorder=4,
        )

    for node in nodes:
        if node.mandatory:
            ax.scatter(
                node.x,
                node.y,
                s=210,
                marker="s",
                facecolor="#111827" if node.node_id in selected else "#9ca3af",
                edgecolor="white",
                linewidth=1.2,
                zorder=5,
            )
        else:
            size = 70 + 720 * (node.value / max_value) ** 1.3
            ax.scatter(
                node.x,
                node.y,
                s=size,
                marker="o",
                facecolor="#f97316" if node.node_id in selected else "#fbbf24",
                edgecolor="#7c2d12" if node.node_id in selected else "#b45309",
                linewidth=1.0,
                alpha=0.92 if node.node_id in selected else 0.45,
                zorder=5,
            )

        label_color = "#111827" if node.node_id in selected or node.mandatory else "#64748b"
        ax.text(node.x + 1.2, node.y + 1.2, node.node_id, fontsize=8, color=label_color, zorder=6)

    spent = sum(edge.cost for edge in edges)
    total_value = captured_pair_value(selected, nodes_map)
    selected_demand_count = sum(1 for node_id in selected if not nodes_map[node_id].mandatory)

    ax.set_title(title, fontsize=15, weight="bold")
    ax.text(
        0.02,
        0.98,
        "\n".join(
            [
                f"budget: {budget:.0f}",
                f"spent: {spent:.1f}",
                f"selected demand nodes: {selected_demand_count}",
                f"captured pair value: {total_value:.1f}",
                "node size = value; edge label = cost",
            ]
        ),
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=10,
        color="#0f172a",
        bbox={"boxstyle": "round,pad=0.45", "facecolor": "white", "edgecolor": "#cbd5e1", "alpha": 0.95},
    )
    ax.set_xlabel("synthetic x coordinate")
    ax.set_ylabel("synthetic y coordinate")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(color="#e2e8f0", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(output_file, dpi=180)
    plt.close(fig)


def write_summary(
    path: Path,
    nodes: list[Node],
    selected: set[str],
    edges: list[Edge],
    budget: float,
    trace: list[dict],
    extra: dict | None = None,
) -> None:
    nodes_map = node_by_id(nodes)
    spent = sum(edge.cost for edge in edges)
    summary = {
        "budget": budget,
        "spent": round(spent, 3),
        "remaining_budget": round(budget - spent, 3),
        "captured_pair_value": round(captured_pair_value(selected, nodes_map), 3),
        "selected_nodes": sorted(selected),
        "selected_demand_nodes": sorted(node_id for node_id in selected if not nodes_map[node_id].mandatory),
        "mandatory_nodes": sorted(node.node_id for node in nodes if node.mandatory),
        "selected_edges": [asdict(edge) | {"cost": round(edge.cost, 3)} for edge in edges],
        "trace": trace,
    }
    if extra:
        summary.update(extra)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the randomized value-connection greedy prototype.")
    parser.add_argument("--seed", type=int, default=11, help="Random seed.")
    parser.add_argument("--demand-nodes", type=int, default=30, help="Number of demand/value nodes.")
    parser.add_argument("--mandatory-nodes", type=int, default=3, help="Number of mandatory zero-value nodes.")
    parser.add_argument("--budget", type=float, default=340.0, help="Total connection-cost budget.")
    parser.add_argument("--width", type=float, default=100.0, help="Synthetic map width.")
    parser.add_argument("--height", type=float, default=100.0, help="Synthetic map height.")
    parser.add_argument("--exact", action="store_true", help="Also run brute-force exact search.")
    parser.add_argument(
        "--max-exact-demand-nodes",
        type=int,
        default=20,
        help="Safety cap for exact search, because it enumerates 2^n demand subsets.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.demand_nodes < 1:
        raise ValueError("--demand-nodes must be at least 1")
    if args.mandatory_nodes < 0:
        raise ValueError("--mandatory-nodes must be nonnegative")
    if args.budget <= 0:
        raise ValueError("--budget must be positive")
    if args.exact and args.demand_nodes > args.max_exact_demand_nodes:
        raise ValueError(
            f"Exact search would enumerate 2^{args.demand_nodes} subsets. "
            f"Use --demand-nodes <= {args.max_exact_demand_nodes}, or increase --max-exact-demand-nodes intentionally."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    nodes = generate_nodes(rng, args.demand_nodes, args.mandatory_nodes, args.width, args.height)
    selected, edges, trace = greedy_connect(nodes, args.budget)

    write_nodes_csv(nodes, OUTPUT_DIR / "nodes.csv")
    write_edges_csv(edges, OUTPUT_DIR / "selected_edges.csv")
    write_summary(OUTPUT_DIR / "summary.json", nodes, selected, edges, args.budget, trace)
    plot_solution(
        nodes,
        selected,
        edges,
        args.budget,
        OUTPUT_DIR / "prototype_greedy.png",
        "Greedy Baseline for Budgeted Value Connection",
    )

    nodes_map = node_by_id(nodes)
    spent = sum(edge.cost for edge in edges)
    greedy_value = captured_pair_value(selected, nodes_map)
    print("Greedy value-connection prototype complete")
    print(f"  nodes: {len(nodes)}")
    print(f"  selected nodes: {len(selected)}")
    print(f"  selected edges: {len(edges)}")
    print(f"  spent: {spent:.2f} / {args.budget:.2f}")
    print(f"  captured pair value: {greedy_value:.2f}")

    if args.exact:
        exact_selected, exact_edges, exact_stats = exact_connect(nodes, args.budget)
        exact_spent = sum(edge.cost for edge in exact_edges)
        exact_value = captured_pair_value(exact_selected, nodes_map)
        write_edges_csv(exact_edges, OUTPUT_DIR / "exact_edges.csv")
        write_summary(
            OUTPUT_DIR / "exact_summary.json",
            nodes,
            exact_selected,
            exact_edges,
            args.budget,
            [],
            {"exact_search": exact_stats},
        )
        plot_solution(
            nodes,
            exact_selected,
            exact_edges,
            args.budget,
            OUTPUT_DIR / "prototype_exact.png",
            "Exact Optimum by Brute-Force Subset Search",
        )
        comparison = {
            "budget": args.budget,
            "demand_nodes": args.demand_nodes,
            "mandatory_nodes": args.mandatory_nodes,
            "greedy": {
                "spent": round(spent, 3),
                "captured_pair_value": round(greedy_value, 3),
                "selected_nodes": sorted(selected),
            },
            "exact": {
                "spent": round(exact_spent, 3),
                "captured_pair_value": round(exact_value, 3),
                "selected_nodes": sorted(exact_selected),
                "checked_subsets": exact_stats["checked_subsets"],
                "feasible_subsets": exact_stats["feasible_subsets"],
            },
            "greedy_optimality_ratio": round(greedy_value / exact_value, 6) if exact_value > 0 else None,
        }
        (OUTPUT_DIR / "comparison_summary.json").write_text(json.dumps(comparison, indent=2), encoding="utf-8")

        print("Exact brute-force search complete")
        print(f"  checked subsets: {exact_stats['checked_subsets']}")
        print(f"  feasible subsets: {exact_stats['feasible_subsets']}")
        print(f"  selected nodes: {len(exact_selected)}")
        print(f"  spent: {exact_spent:.2f} / {args.budget:.2f}")
        print(f"  optimal pair value: {exact_value:.2f}")
        if exact_value > 0:
            print(f"  greedy / exact: {greedy_value / exact_value:.3f}")
    print(f"  output: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
