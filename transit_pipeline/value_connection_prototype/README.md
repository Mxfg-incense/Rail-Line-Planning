# Value Connection Prototype

This folder visualizes the simplified graph model used in the proposal:

- demand nodes have value `v_i`;
- mandatory nodes must be connected but have zero reward;
- the candidate connection graph is complete;
- edge cost `w_ij` is Euclidean distance;
- connecting two selected demand nodes generates value `v_i * v_j / dist(i,j)`;
- a greedy baseline grows a connected network under a total cost budget.

Run from the workspace root:

```powershell
uv run python .\transit_pipeline\value_connection_prototype\run_greedy_prototype.py
```

Useful options:

```powershell
uv run python .\transit_pipeline\value_connection_prototype\run_greedy_prototype.py --seed 7 --demand-nodes 28 --mandatory-nodes 3 --budget 360
```

For a small instance, run brute-force search to get the exact optimum and compare
it with greedy:

```powershell
uv run python .\transit_pipeline\value_connection_prototype\run_greedy_prototype.py --seed 7 --demand-nodes 16 --mandatory-nodes 3 --budget 230 --exact
```

The exact mode enumerates all demand-node subsets, so keep `--demand-nodes`
small. The default safety cap is 20 demand nodes.

Outputs are written to `transit_pipeline/value_connection_prototype/output/`:

- `prototype_greedy.png`: visualization of node values and selected network;
- `prototype_exact.png`: exact optimum visualization when `--exact` is used;
- `nodes.csv`: randomized node coordinates and values;
- `selected_edges.csv`: selected edges and costs;
- `exact_edges.csv`: exact selected edges when `--exact` is used;
- `summary.json`: budget, captured value, selected nodes, and algorithm trace.
- `exact_summary.json`: exact solution summary when `--exact` is used;
- `comparison_summary.json`: greedy-vs-exact comparison when `--exact` is used.
