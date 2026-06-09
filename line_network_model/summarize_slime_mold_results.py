#!/usr/bin/env python3
"""Write a compact comparison table for slime-mold variants."""

from __future__ import annotations

import csv
from pathlib import Path

from line_network_model.line_model_config import OUTPUT_DIR


COMPARISON_FILE = OUTPUT_DIR / "13_slime_mold_model_comparison.csv"
VARIANTS = [
    (
        "physarum_sparse",
        "Original Physarum extraction tuned for a clean sparse skeleton.",
        OUTPUT_DIR / "10_slime_mold_sparse_summary.csv",
    ),
    (
        "physarum_coverage",
        "Original Physarum extraction tuned for station coverage; used as the baseline before augmentation.",
        OUTPUT_DIR / "10_slime_mold_summary.csv",
    ),
    (
        "physarum_grid_augmented",
        "Physarum coverage skeleton plus orientation-aware vertical corridor augmentation.",
        OUTPUT_DIR / "12_slime_mold_grid_augmented_summary.csv",
    ),
]
FIELDS = [
    "variant",
    "description",
    "selected_stations",
    "selected_edges",
    "total_length_km",
    "max_edge_km",
    "branch_nodes",
    "leaf_nodes",
    "horizontal_edges",
    "vertical_edges",
    "diagonal_edges",
    "horizontal_edge_share",
    "vertical_edge_share",
    "covered_value",
    "added_edges",
    "base_selected_stations",
    "base_vertical_edges",
]


def read_one(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run the slime-mold scripts first.")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 1:
        raise ValueError(f"{path} should contain exactly one summary row, got {len(rows)}.")
    return rows[0]


def compact_row(name: str, description: str, path: Path) -> dict:
    source = read_one(path)
    row = {"variant": name, "description": description}
    for field in FIELDS:
        if field in row:
            continue
        row[field] = source.get(field, "")
    return row


def main() -> None:
    rows = [compact_row(name, description, path) for name, description, path in VARIANTS]
    with COMPARISON_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print("Slime mold comparison complete")
    print(f"  variants: {len(rows)}")
    print(f"  csv: {COMPARISON_FILE}")


if __name__ == "__main__":
    main()
