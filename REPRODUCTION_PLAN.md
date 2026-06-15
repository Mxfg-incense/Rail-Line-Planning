# Reproduction Plan: Two-Paper Replication

## Target Papers

### Paper 1: Laporte et al. 2005
**"Maximizing Trip Coverage in the Location of a Single Rapid Transit Alignment"**
*Annals of Operations Research*, 136, 299–314.

- **Problem**: Single-line MTCP (Maximal Trip Coverage Problem) — build one non-intersecting path through candidate stations to maximize OD trip coverage under a length budget.
- **Algorithms**: H1 (Greedy Extension), H2-A/B/C (Greedy Insertion variants), Post-optimization.
- **Key result**: H1 dominates all H2 variants for inter-station spacing ≥ 1250m.

### Paper 2: Ahmed et al. 2020
**"GIS and Genetic Algorithm Based Integrated Optimization for Rail Transit System Planning"**
*Journal of Rail Transport Planning & Management*, 16, 100222.

- **Problem**: Multi-line rail transit network design — simultaneously select stations and line network to minimize total system cost (passenger + operator + community).
- **Algorithms**: Two-stage: (1) GIS-based feasibility screening, (2) GA with tournament selection, uniform crossover, mutation with feasibility repair.
- **Key results**: Simultaneous optimization 70% better than individual; optimal Pc=0.7, Pm=0.3; pop=500, gen=50.

---

## Environment Setup

All work must be done in a project-specific virtual environment managed by `uv`.

```bash
cd /mnt/luyuzhou/CS240/Rail-Line-Planning
uv venv
source .venv/bin/activate
uv sync
```

All scripts are run as:
```bash
uv run python -m line_network_model.<module>
```

---

## Part A: Laporte 2005 Reproduction

### A.1 What to Reproduce

| Item | Paper Reference | Our Implementation |
|---|---|---|
| MTCP formulation | Section 3 | `laporte2005.py` — `MTCPInstance` class |
| H1: Greedy Extension | Section 3, H1 | `LaporteHeuristics.h1_greedy_extension()` |
| H2-A: Greedy Insertion (max ridership) | Section 3, H2 Variant A | `LaporteHeuristics.h2_greedy_insertion(variant='A')` |
| H2-B: Greedy Insertion (min length) | Section 3, H2 Variant B | `LaporteHeuristics.h2_greedy_insertion(variant='B')` |
| H2-C: Greedy Insertion (max ratio) | Section 3, H2 Variant C | `LaporteHeuristics.h2_greedy_insertion(variant='C')` |
| Post-optimization | Section 3.1 | `LaporteHeuristics.post_optimize()` |
| Table 1 (simulated results) | Section 4.1 | `experiment_laporte2005.py` — Shanghai + toy data |
| Sevilla real-data results | Section 4.2 | Shanghai 54-station dataset with varied spacing |

### A.2 Simplifications (Justified)

| Paper | Our Adaptation | Justification |
|---|---|---|
| Census-tract-based f_ij | Gravity-model OD matrix (`od.py`) | Paper: "heuristics can work independently of the model used to estimate O/D demand" (p.7) |
| Logit mode choice g_ij(E) | g_ij = 1 (pure OD coverage) | Paper's Table 1 uses this simpler measure; logit adds calibration without changing algorithmic structure |
| Complete graph for candidate edges | Corridor graph from `corridor.py` | Practical refinement; still allows all pairwise connections |
| Sevilla 21/164 nodes | Shanghai 54 station candidates | Tests generalizability |

### A.3 Module Structure

```
line_network_model/
├── laporte2005.py          # MTCPInstance, LaporteHeuristics (H1, H2-A/B/C, post-opt)
├── experiment_laporte2005.py  # Runnable experiment script
└── output/
    └── laporte2005_reproduction/
        ├── reproduction_results.csv    # Table 1 equivalent
        ├── alignment_h1.png
        ├── alignment_h2a.png
        ├── alignment_h2b.png
        ├── alignment_h2c.png
        ├── spacing_sensitivity.csv     # Inter-station spacing experiment
        └── runtime_scalability.csv     # n vs. runtime
```

### A.4 Experiments

1. **Table 1 Reproduction** — Run H1, H2-A, H2-B, H2-C on Shanghai data with LMAX ∈ {40, 60, 80} km. Report: length, ridership, ratio, runtime.
2. **Spacing Sensitivity** — Vary min station spacing {500, 750, 1000, 1250, 1500}m. Test hypothesis: H1 dominates for ≥1250m.
3. **Runtime Scalability** — Sweep n ∈ {20, 30, 40, 50, 60, 80, 100} using toy generator. Plot n vs. runtime.
4. **Comparison with dev-OD methods** — Compare H1 result against existing `select_lines_greedy_od`, `select_lines_greedy_bcr`, `select_lines_ga`, `select_lines_ilp`.

### A.5 Complexity Analysis

| Algorithm | Per-iteration Cost | Total Worst-Case |
|---|---|---|
| H1 | O(n · |E|²) — evaluate all unselected nodes at both endpoints | O(n² · |E|²) |
| H2-A/B/C | O(n · |E|² · |E|) — evaluate all unselected nodes at all insertion positions | O(n² · |E|³) |
| Post-opt | O(|E|² · n) per pass | O(|E|³ · n) |

MTCP generalizes bounded-length Hamiltonian path → NP-hard. All heuristics are polynomial-time.

---

## Part B: Ahmed 2020 Reproduction

### B.1 What to Reproduce

| Item | Paper Reference | Our Implementation |
|---|---|---|
| Stage 1: Feasibility screening | Section 3.1, Eqs (1)–(9) | Reuse our `01_select_stations.py` pipeline (same purpose: identify candidate stations from spatial layers) |
| Stage 2: GA chromosome encoding | Section 3.2.1, Fig. 3 | `ahmed2020.py` — `MultiLineGA` class |
| Fitness: Total system cost | Section 3.2.2, Eqs (10)–(25) | Simplified 3-component cost (see B.2) |
| Tournament selection | Section 3.2.3(i) | `_tournament_select()` |
| Uniform crossover | Section 3.2.3(ii), Fig. 4 | `_uniform_crossover()` with feasibility repair |
| Station-level mutation | Section 3.2.3(iii), Fig. 5 | `_mutate()` with feasibility repair |
| Simultaneous vs. Individual | Section 5.4, Table 3 | Run both modes, compare total cost |
| Crossover rate sensitivity | Section 5.2, Fig. 8 | Sweep Pc ∈ {0.5, 0.6, 0.7, 0.8, 0.9, 1.0} |
| Mutation rate sensitivity | Section 5.3, Fig. 9 | Sweep Pm ∈ {0.1, 0.2, 0.3, 0.4, 0.5, 0.6} |
| Goodness evaluation | Section 6, Table 4 | Compare GA solution against random/ heuristic baselines |

### B.2 Simplifications (Justified)

The Ahmed 2020 paper requires extensive UK-specific input data (car/bus OD matrices, land values, census data, economic action plan, environmental layers, etc.). We make the following well-motivated simplifications:

| Paper Requirement | Our Adaptation | Justification |
|---|---|---|
| GIS feasibility screening (8 criteria, 7027 cells) | Our `01_select_stations.py` pipeline (54 stations from 4 layers) | Same conceptual purpose: filter candidate locations by spatial criteria. The paper's method is a grid-based GIS filter; ours is a clustering-based filter. Both produce a candidate station pool for the GA. |
| Car/bus OD matrices + logit mode choice | Gravity-model OD + simple distance-based mode split | We lack Leicester's multi-modal OD data. Gravity model preserves OD demand capture logic. |
| Passenger cost (Eq.10): detailed multi-modal time costs | Simplified: Δtravel_time · value_of_time · OD_flow | Preserves the structure: passenger benefit = time saved vs. alternative modes |
| Operator cost (Eq.18): detailed per-mode O&M costs | Simplified: distance_based operating cost per passenger-km | Preserves the structure: operator cost proportional to service distance and ridership |
| Community cost (Eq.20–25): land acquisition + tunnel + station + track | Our corridor graph construction_cost (distance-based) + per-station fixed cost | Preserves the structure: construction cost depends on alignment length and station count |
| Leicester-specific parameters (Table 1) | Default values from paper, made configurable | Allows sensitivity analysis |
| Terminal stations pre-specified by planners | Top-K high-value station pairs OR highest-OD pairs | The paper assumes planners specify terminals; we use data-driven selection |

### B.3 Chromosome Encoding

Following the paper's Fig. 3:

```
Chromosome = [Line_1_genes, Line_2_genes, ..., Line_L_genes]
Line_k_genes = [terminal_a, station_seq..., terminal_b]
```

- Terminal stations are fixed (given by planner or selected from high-value pairs)
- Intermediate stations are selected from the candidate pool (our 54 stations)
- Each gene is a station index from the candidate pool
- Chromosome length = Σ (stations_per_line_k)

### B.4 Fitness Function (Simplified)

```
TotalCost = φp · PassengerCost + φo · OperatorCost + φc · CommunityCost

PassengerCost = - Σ_{i,j} OD_ij · (travel_time_car_ij - travel_time_metro_ij) · value_of_time
  (negative means savings — paper reports negative passenger costs)

OperatorCost = - (bus_op_cost_per_pax_km - rail_op_cost_per_pax_km) · total_passenger_km
  (savings from mode shift)

CommunityCost = construction_cost_per_km · total_line_length + station_cost · num_stations
```

### B.5 Constraints (from paper Table 1)

| Constraint | Paper Value | Our Configurable Default |
|---|---|---|
| Min stations per line | 4 | 4 |
| Max stations per line | 7 | 10 |
| Min station spacing | 800 m | 1000 m |
| Max station spacing | 1500 m | 3000 m |
| Min transfer stations (per line) | 1 | 1 |
| Max overlap between lines | 30% | 30% |

### B.6 GA Parameters (from paper Table 1 + sensitivity analysis)

| Parameter | Paper Value |
|---|---|
| Population size | 500 |
| Generations | 50 |
| Crossover rate | 0.7 |
| Mutation rate | 0.3 |
| Tournament size | 2 |

For our smaller candidate set (54 vs. 4774), we may scale down population size.

### B.7 Module Structure

```
line_network_model/
├── ahmed2020.py              # MultiLineGA, cost functions, chromosome encoding
├── experiment_ahmed2020.py   # Runnable experiment script
└── output/
    └── ahmed2020_reproduction/
        ├── ga_convergence.csv         # Generation vs. best fitness
        ├── cost_breakdown.csv         # Passenger, operator, community costs
        ├── simultaneous_vs_individual.csv  # Table 3 equivalent
        ├── crossover_sensitivity.csv  # Fig. 8 equivalent
        ├── mutation_sensitivity.csv   # Fig. 9 equivalent
        ├── goodness_evaluation.csv    # Table 4 equivalent
        ├── network_1line.png          # Fig. 7a equivalent
        ├── network_2line.png          # Fig. 7b equivalent
        ├── network_3line.png          # Fig. 7c equivalent
        └── runtime_scalability.csv
```

### B.8 Experiments

1. **Basic GA Run** — 1, 2, and 3 line scenarios on Shanghai data. Report convergence curve, cost breakdown, final network visualization.
2. **Simultaneous vs. Individual** — Run both modes for 2-line system. Compare total cost (expect ~70% improvement for simultaneous).
3. **Crossover Rate Sensitivity** — Sweep Pc ∈ {0.5, 0.6, 0.7, 0.8, 0.9, 1.0}. Plot total cost vs. Pc.
4. **Mutation Rate Sensitivity** — Sweep Pm ∈ {0.1, 0.2, 0.3, 0.4, 0.5, 0.6}. Plot total cost vs. Pm.
5. **Goodness Evaluation** — Generate 100 random feasible solutions; compare GA best fitness against random distribution.
6. **Runtime Scalability** — Vary candidate station count n ∈ {20, 30, 40, 54}. Measure GA runtime.
7. **Comparison with dev-OD methods** — Compare Ahmed GA against existing `select_lines_ga`, `select_lines_ilp`.

### B.9 Complexity Analysis

| Component | Complexity |
|---|---|
| Stage 1 (feasibility screening) | O(N_cells · K_criteria) — one-time preprocessing |
| Chromosome initialization | O(P · L · Ns) per generation |
| Fitness evaluation (per chromosome) | O(L · Ns² · log Ns) — Dijkstra shortest paths for travel times |
| Tournament selection | O(P) per generation |
| Uniform crossover | O(P · L · Ns) per generation |
| Mutation | O(P · L · Ns · Pm) per generation |
| **Total per generation** | O(P · L · Ns² · log Ns) |
| **Total** | O(G · P · L · Ns² · log Ns) |

Where P = population size, G = generations, L = number of lines, Ns = stations per line.

---

## Shared Infrastructure (Both Papers)

Both reproductions share these existing modules from `dev-OD`:

| Module | Used By | Purpose |
|---|---|---|
| `data.py` | Both | Load stations (Shanghai or toy) |
| `od.py` | Both | Generate gravity-model OD matrix |
| `corridor.py` | Both | Build candidate corridor graph |
| `evaluation.py` | Both | Compute coverage, efficiency, transfer metrics |
| `selection.py` | Ahmed 2020 | Baseline comparison (existing GA, ILP) |

---

## Implementation Order

### Phase 1: Laporte 2005 (simpler, builds foundation)
1. `laporte2005.py` — `MTCPInstance` + H1
2. `laporte2005.py` — H2-A, H2-B, H2-C
3. `laporte2005.py` — Post-optimization
4. `experiment_laporte2005.py` — Table 1 reproduction
5. Spacing sensitivity + runtime scalability experiments

### Phase 2: Ahmed 2020 (builds on Phase 1 infrastructure)
6. `ahmed2020.py` — Chromosome encoding + initialization
7. `ahmed2020.py` — Simplified cost functions (passenger, operator, community)
8. `ahmed2020.py` — GA operators (tournament selection, uniform crossover, mutation)
9. `ahmed2020.py` — Main GA loop with convergence tracking
10. `experiment_ahmed2020.py` — Multi-line scenarios (1, 2, 3 lines)
11. Simultaneous vs. individual comparison
12. Crossover + mutation sensitivity analysis
13. Goodness evaluation

### Phase 3: Cross-paper Analysis
14. Compare Laporte H1 (single-line optimal path) against Ahmed GA (single-line mode)
15. Unified runtime/scalability comparison across all methods
16. Report writing

---

## Experiment Quick Reference

```bash
# Environment setup (once)
uv venv
source .venv/bin/activate
uv sync

# Laporte 2005
uv run python -m line_network_model.experiment_laporte2005
uv run python -m line_network_model.experiment_laporte2005 --toy --lmax 60
uv run python -m line_network_model.experiment_laporte2005 --spacing-sweep

# Ahmed 2020
uv run python -m line_network_model.experiment_ahmed2020
uv run python -m line_network_model.experiment_ahmed2020 --lines 2
uv run python -m line_network_model.experiment_ahmed2020 --lines 3
uv run python -m line_network_model.experiment_ahmed2020 --sensitivity-crossover
uv run python -m line_network_model.experiment_ahmed2020 --sensitivity-mutation
uv run python -m line_network_model.experiment_ahmed2020 --goodness
```
