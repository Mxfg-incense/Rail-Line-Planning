#!/usr/bin/env python3
"""Run the whole line-network-model workflow."""

from __future__ import annotations

import runpy
from pathlib import Path


HERE = Path(__file__).resolve().parent
STEPS = [
    "00_prepare_demand_data.py",
    "01_select_stations.py",
    "02_fetch_existing_metro_reference.py",
    "run_baseline.py",
]


def main() -> None:
    for step in STEPS:
        print(f"\n=== {step} ===")
        runpy.run_path(str(HERE / step), run_name="__main__")


if __name__ == "__main__":
    main()
