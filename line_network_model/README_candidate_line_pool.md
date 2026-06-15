# Candidate Line Pool Experiment Framework

This folder implements a modular rail-transit planning experiment based on
candidate corridors, candidate line pools, and line selection.

## Modules

- `data.py`: loads `01_candidate_stations_table.csv`, normalizes `x/y` or `lon/lat`, and can generate a 50-station toy dataset.
- `od.py`: builds a gravity-model OD matrix with `generate_od_matrix(stations, alpha=1.5, normalize=True)`.
- `corridor.py`: builds candidate corridor graphs with KNN, radius, or MST-plus-short-edges methods.
- `line_pool.py`: generates candidate lines from top OD pairs, high-value terminal pairs, and random walks.
- `selection.py`: selects lines with greedy OD, greedy benefit-cost ratio, a lightweight GA, and an optional `pulp` ILP baseline.
- `evaluation.py`: computes cost, coverage, OD, travel-efficiency, transfer, structure, and robustness metrics.
- `experiment.py`: runs the end-to-end workflow and writes CSV/PNG outputs.

## Run

Use the real station table:

```powershell
uv run python -m line_network_model.experiment --max-lines 8
```

Use the toy 50-station dataset:

```powershell
uv run python -m line_network_model.experiment --toy --max-lines 8
```

Outputs are written to:

```text
line_network_model/output/candidate_line_pool_experiment/
```

Key outputs:

- `candidate_line_pool.csv`
- `evaluation_results.csv`
- `selected_lines_<method>.csv`
- `candidate_corridors.png`
- `network_<method>.png`

## Corridor Notes

The default MST-plus corridor builder is not a pure MST. It now uses:

- an MST backbone to guarantee connectivity;
- a global pool of short extra edges;
- a per-station minimum degree safeguard, default `min_degree=3`;
- a boundary-station reinforcement, default `edge_min_degree=4` for the outer 20% of stations.

This avoids a common issue where all extra edges concentrate in the dense urban core while southern
or other peripheral stations remain connected by only one tree edge.
