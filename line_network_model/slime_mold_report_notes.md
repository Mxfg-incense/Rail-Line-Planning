# Slime Mold Model Notes

This note summarizes the Physarum-inspired baseline and the later
orientation-aware post-processing used for the line-network project.

## Motivation

The earlier greedy and genetic baselines optimize a hand-written objective
function directly. The slime-mold model gives a different classical algorithmic
baseline: it treats the candidate station graph as a transport network whose
edge conductances are reinforced by repeated flow between high-value origin and
destination pairs.

This is useful for the report because it is not just another parameter setting
of the same objective. It is a bio-inspired network formation algorithm that can
be compared against the greedy/manual/objective-calibrated approaches under the
same evaluation metrics.

## Method

The implementation is in `run_slime_mold_baseline.py`.

1. Build a candidate graph from the station set.
   - Each candidate station is connected to nearby stations using a k-nearest
     and maximum-distance rule.
   - Initial conductance is slightly higher for links between high-value
     stations.

2. Select OD pairs.
   - Station pairs with high value and reasonable distance are selected as
     source-sink pairs.
   - Each OD pair injects a small amount of normalized demand into the graph.

3. Iterate a Physarum-style update.
   - For each OD pair, solve node pressures on the weighted graph.
   - Edge flow is computed from conductance, pressure difference, and length.
   - Conductance decays unless reinforced by flow:

```text
conductance_next =
    (1 - decay) * conductance_current
    + reinforcement * normalized_flow
```

4. Extract a rail skeleton.
   - Keep high-conductance edges.
   - Prefer edges serving high-value stations.
   - Repair disconnected components with short paths through the candidate
     graph.
   - Prune weak non-protected leaves.

## Post-Processing

The raw Physarum result produced a plausible demand-driven skeleton, but it was
too weak in the north-south direction. This matched the visual diagnosis that a
metro network should contain both horizontal and vertical corridors rather than
only a single tree-like trunk.

The orientation-aware augmentation is implemented in
`augment_slime_mold_grid.py`.

It starts from the coverage-tuned Physarum skeleton and adds a small number of
vertical candidate edges, subject to simple planning constraints:

- the network must remain connected;
- branch-node count must not increase beyond the configured limit;
- maximum edge length must remain below the configured threshold;
- the added edge must attach to the existing skeleton;
- the process stops after reaching the target vertical-edge count or edge
  budget.

This keeps the classical Physarum result as the primary baseline while adding a
planning-aware correction for metro-grid structure.

## Evaluation Metrics

The outputs are evaluated using metrics that are intentionally separate from the
internal Physarum update rule:

- selected stations;
- selected edges;
- total construction length;
- maximum edge length;
- branch nodes;
- leaf nodes;
- horizontal, vertical, and diagonal edge counts;
- horizontal and vertical edge shares.

This separation is important. The Physarum algorithm optimizes conductance and
flow reinforcement internally, while the evaluation metrics describe whether the
result looks like a useful rail network.

## Results

The compact comparison table is written by `summarize_slime_mold_results.py`:

```powershell
uv run python -m line_network_model.summarize_slime_mold_results
```

Output:

- `output/13_slime_mold_model_comparison.csv`

Current key results:

| Variant | Stations | Edges | Length km | Branch nodes | Leaf nodes | Horizontal edges | Vertical edges | Diagonal edges |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Physarum sparse | 22 | 21 | 40.88 | 1 | 3 | 9 | 2 | 10 |
| Physarum coverage | 26 | 25 | 49.18 | 3 | 5 | 9 | 2 | 14 |
| Physarum grid augmented | 30 | 30 | 63.11 | 3 | 3 | 9 | 7 | 14 |

The main improvement from coverage to grid-augmented is:

```text
selected_stations: 26 -> 30
selected_edges:    25 -> 30
vertical_edges:    2  -> 7
branch_nodes:      3  -> 3
leaf_nodes:        5  -> 3
```

Thus, the augmented version improves station coverage and vertical structure
without increasing branch complexity.

## Report Wording

One concise way to describe the method:

> We implemented a Physarum-inspired network formation baseline in which
> high-value OD pairs induce pressure-driven flow over a candidate station graph.
> Edge conductance decays over time unless reinforced by repeated flow, causing a
> demand-driven rail skeleton to emerge. Since the raw Physarum skeleton tended
> to under-represent north-south corridors, we added an orientation-aware
> augmentation step that inserts a small number of vertical links under branch
> and length constraints. This produced a more metro-like grid while preserving
> the same branch count.

## Commands

Regenerate the main Physarum variants:

```powershell
uv run python -m line_network_model.calibrate_slime_mold_parameters
```

Regenerate the grid-augmented result:

```powershell
uv run python -m line_network_model.augment_slime_mold_grid
```

Regenerate the comparison table:

```powershell
uv run python -m line_network_model.summarize_slime_mold_results
```
