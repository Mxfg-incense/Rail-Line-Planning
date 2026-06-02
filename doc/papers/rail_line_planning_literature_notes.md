# Rail Transit Line Planning Literature Notes

Date: 2026-05-16

Scope: papers in this folder plus a small number of additional works found during targeted search. I focus on line alignment, station layout, network design, and local line extension. Papers mainly about crew scheduling, map drawing, or pure operations are marked low relevance.

## Executive Takeaways

For our CS240 project, the strongest formulation is:

> Given an existing rail network, OD demand, population/jobs/activity proxies, candidate stations, and candidate corridors, plan one new line or local extension under length/cost constraints to maximize captured demand and improve network connectivity.

The most project-ready algorithmic framing is:

1. Candidate graph construction from roads, existing stations, POIs, population/job centers.
2. Candidate line generation using shortest path / k-shortest paths / greedy insertion.
3. Line scoring by OD demand capture, covered population/jobs, transfer improvement, and construction cost.
4. Optimization using budgeted maximum coverage, prize-collecting path, greedy heuristic, simulated annealing, or genetic algorithm.
5. Evaluation by all-or-nothing or logit passenger assignment on the old vs. old+new network.

The closest papers for our intended direction are:

- Chai et al. 2019: road network + trips + land use constrained urban rail network design.
- Ahmed/Xu et al. 2020: GIS + GA integrated station and line alignment optimization, Leicester case.
- Laporte et al. 2005: single rapid transit alignment maximizing OD trip coverage, Sevilla case.
- Gutierrez-Jarpa et al. 2013/2015: rapid transit network design with OD demand capture and modal competition, Concepcion case.
- He et al. 2024: local line optimization in an existing Beijing URT network with passenger flow allocation.
- Yin & Peng 2023: station layout and route selection around Shanghai Pudong International Airport.

## High-Relevance Papers

### Chai et al. 2019, Design of Urban Rail Transit Network Constrained by Urban Road Network, Trips and Land-Use Characteristics

Local file: `chai_2019_urban_rail_network_road_trips_land_use.pdf`

Actual city/case: real case study in a Chinese city central area; also uses Beijing metro as an example for transfer-time analysis.

Input data:

- Basic network derived from urban road network, high-traffic locations, transport hubs, district centers, and main passenger-flow corridors.
- Candidate station set and candidate sections.
- OD passenger demand between major traffic-volume locations.
- Trip production/attraction by traffic zone.
- Land-use intensity by zone.
- Coordinates of candidate stations.
- Section lengths and section capacity.
- Planning bounds: min/max line length, min/max station number per line, min/max number of lines.

Optimization objective:

- Objective 1: maximize passenger turnover per unit network length.
- Objective 2: minimize average number of transfer passengers between lines.
- Converted to weighted single objective: maximize beta1 * transport efficiency - beta2 * transfer burden.

Constraints:

- Topology: selected sections must connect selected stations; lines have no branches or loops.
- Repetition: station and section repeatability limits.
- Passenger flow balance for OD paths.
- Matching constraints: rail network must match urban trips and land-use intensity above thresholds.
- Alignment smoothness: minimum angle between adjacent sections.
- Network size: min/max stations, line length, and number of lines.
- Section capacity constraints.

Method:

- Build a basic network to restrict the search space and force rail lines to follow plausible corridors.
- Use centrality-based station importance.
- Define trip matching by deviation between gravity center of station-importance distribution and gravity center of trip intensity.
- Define land-use matching by fractal-dimension similarity.
- Solve NP-hard model with a simulated-annealing neighborhood search.
- Neighborhood operators: add/delete line, add/delete section.

Usefulness for us:

- Very close to our idea: road network, OD trips, land use, transfer efficiency, and capacity.
- We can simplify it into a single-line extension problem and use its objective/constraint ideas.

### Ahmed et al. 2020, GIS and Genetic Algorithm Based Integrated Optimization for Rail Transit System Planning

Local file: `xu_2020_gis_genetic_algorithm_rail_transit_planning.pdf`

Actual city/case: Leicester, UK.

Input data:

- GIS layers: land use, existing transport network, census, land values, economic action plan, topography.
- Environmentally sensitive areas: historic buildings, parks, woodlands, rivers.
- OD trip matrices from Leicester City Council.
- Car, bus, and active-mode demand matrices from external regional demand model.
- Candidate station feasibility grids.
- Terminal station pairs and desired topology, supplied by planners.
- Cost parameters: station cost, tunnel cost, right-of-way/land acquisition, track cost, train headway, access/waiting/in-vehicle time values.

Optimization objective:

- Minimize total life-cycle system cost:
  - passenger cost,
  - operator cost,
  - community/construction cost.
- Passenger cost compares rail travel time cost against car/bus alternatives.
- Operator cost compares operating and maintenance costs.
- Community cost includes land acquisition, tunnel construction, station construction/equipment, and track construction.

Constraints:

- Min/max number of stations per line.
- Min/max distance between consecutive stations.
- Minimum number of transfer/intersected stations.
- Maximum overlap/common sections between two lines.
- Candidate station feasibility thresholds from demographic/topological/economic/land-use criteria.
- Environmental exclusions.

Method:

- Stage 1: feasibility screening for candidate station cells in GIS.
- Stage 2: genetic algorithm jointly selects station subset and line network.
- Chromosome: whole solution; genes: rail lines; bits: stations.
- Fitness: total system cost.
- Uses tournament selection, uniform crossover, mutation with feasibility repair/rejection.
- Uses Dijkstra for shortest travel distance between station pairs inside fitness calculation.

Usefulness for us:

- Strong template for data pipeline and GIS feature engineering.
- The full GA model is large; for CS240, we can reproduce a smaller classical version: candidate graph + greedy/shortest-path/GA line search.

### Laporte et al. 2005, Maximizing Trip Coverage in the Location of a Single Rapid Transit Alignment

Local file: `laporte_2005_maximizing_trip_coverage_single_rapid_transit_alignment.pdf`

Actual city/case: Sevilla, Spain.

Input data:

- Candidate station nodes.
- OD trip matrix between zones/stations.
- Census tract/population density data.
- Catchment rings around stations.
- Walking-distance metric.
- Competing mode travel time, represented as private car on street grid.
- Maximum alignment length.

Optimization objective:

- Maximize total OD trip demand captured by the alignment.
- Uses a logit function to estimate whether trips between two stations choose rapid transit over car.

Constraints:

- Alignment length must not exceed a maximum length.
- Non-intersecting alignment.
- Station set selected from candidate nodes.

Method:

- Formulates maximal trip coverage problem as NP-hard.
- Proposes constructive heuristics:
  - Greedy extension of an alignment.
  - Greedy insertion variants.
  - Post-optimization.
- Computational results on Sevilla show greedy extension works well when inter-station upper bound is relaxed; insertion + post-optimization works better when station-spacing upper bounds are tighter.

Usefulness for us:

- This is the cleanest single-line planning model.
- Very suitable for a CS240 reproduction: simple graph/path heuristic, OD coverage objective, and real or synthetic OD.

### Gutierrez-Jarpa et al. 2013/2015, Rapid Transit Network Design for OD Demand Capture / MILP with Modal Competition

Local file: `cadarso_2015_milp_rapid_transit_network_design_concepcion.pdf` is a short ATMOS paper summarizing the modal competition extension. The 2013 Computers & Operations Research paper was identified via ScienceDirect/repository but not downloaded in full.

Actual city/case: Concepcion, Chile.

Input data:

- Undirected graph of potential station locations and candidate edges/corridors.
- OD demand flows.
- Broad corridors/topological configurations suggested by planners.
- Construction cost/budget.
- Competing car/current-network generalized travel cost.

Optimization objective:

- Minimize travel/construction cost and maximize OD demand capture.
- In modal competition version: OD flow is captured only if rapid transit travel time/generalized cost is lower than car/current mode.
- Multi-objective framework supports effectiveness, efficiency, and equity post-analysis.

Constraints:

- Budget constraint.
- Pre-assigned broad topology/corridor constraints.
- Station and segment construction decisions.
- Modal competition/all-or-nothing capture condition.

Method:

- Integer linear programming / MILP.
- Branch-and-cut in CPLEX for the 2013 model.
- Pre-assigned topology reduces combinatorial complexity while keeping practical planning structure.

Usefulness for us:

- Strong justification for using OD demand capture rather than just population coverage.
- Their modal competition idea can be simplified into: a demand pair is counted if travel time decreases enough after adding the new line.

### He et al. 2024, A Local Line Optimization Model for Urban Rail Considering Passenger Flow Allocation

No local PDF because Springer blocked direct command-line download, but the open-access article was accessible in browser.

Source: https://link.springer.com/article/10.1007/s40864-024-00212-w

Actual city/case: Beijing URT local network.

Input data:

- Existing URT network.
- Bottleneck OD pairs with high travel demand but low URT usage.
- Urban hierarchy road network and conventional bus network.
- Candidate stations selected near road intersections and bus stops.
- Population covered within 800 m of stations.
- Network distance between stations.
- Estimated construction/engineering investment.

Optimization objective:

- Multi-objective: maximize passenger flow and minimize project cost.
- Converted into a single-objective route generation model.

Constraints:

- Station interval.
- Terminal stations.
- OD points and mandatory stops.
- Practical route constraints from road network.
- All passengers assigned by shortest path/all-or-nothing passenger flow allocation.

Method:

- Identify bottleneck regions in existing rail network.
- Select candidate stations using road hierarchy and bus network.
- Embed passenger flow allocation in an elitist genetic algorithm.
- Evaluate line schemes on updated old+new network.

Usefulness for us:

- This is extremely close if we want "based on existing lines" rather than greenfield planning.
- The all-or-nothing assignment is simple and reproducible.

### Yin & Peng 2023, Station Layout Optimization and Route Selection of Urban Rail Transit Planning: Shanghai Pudong International Airport

Local file: `yin_2023_station_layout_route_selection_shanghai_pudong_airport.pdf`

Actual city/case: Shanghai Pudong International Airport region.

Input data:

- Population density / high-population data points in southwest Pudong airport area.
- POIs in Shanghai.
- Land-use types in Pudong New Area.
- Geological/topographic constraints, including 12.5 m DEM from ALOS PALSAR.
- Road network and arterial roads.
- Airport throughput / contextual demand motivation.

Optimization objective:

- Cost-oriented station spacing optimization.
- Find station clusters/ideal locations and propose route schemes.
- Not a rigorous network-flow objective; more spatial-data driven.

Constraints / planning rules:

- Optimal station spacing around 683.6 m, rounded to 680 m.
- Stations should be near major traffic arterials and about 300 m from large residential/commercial/factory/sports facilities.
- Avoid poor geological areas, wetland protection areas, cultivated land, groundwater sources.
- Prefer road intersections and feasible land-use areas.

Method:

- Build station-spacing cost model from construction cost, operating cost, and generated economic benefit.
- Use HDBSCAN to cluster high-population areas.
- Compare HDBSCAN with DBSCAN and K-means.
- Combine ideal station locations, station spacing, land use, DEM, and road network to produce five candidate route schemes.

Usefulness for us:

- Useful for station candidate generation and GIS preprocessing.
- Less useful as the main algorithm because route selection is more rule-based than optimization-based.

### Lopez-Ramos et al. 2017, Integrated Approach to Network Design and Frequency Setting Problem in Railway Rapid Transit Systems

Local file: `lopez_ramos_2017_network_design_frequency_setting_railway_rapid_transit.pdf`

Actual city/case: Seville, Spain and Santiago de Chile.

Input data:

- Rapid Transit Graph: operating corridors, candidate corridors, candidate stations, existing and candidate lines.
- Passenger Flow Network for assignment.
- OD demand vector.
- Station construction costs and corridor construction costs.
- Vehicle capacity, fleet, line frequencies, stretch capacity.
- Walking times between nearby stations.
- Planning budgets for infrastructure and vehicles.

Optimization objective:

- Lexicographic goal programming:
  1. minimize passenger riding time,
  2. then minimize operator cost while preserving the best passenger goal.
- Integrates network design and frequency setting.

Constraints:

- Infrastructure budget.
- Maximum number of new lines.
- Vehicle capacity.
- Fleet size / vehicle acquisition budget.
- Maximum stretch frequency.
- Passenger assignment and transfer constraints.
- Level-of-service / waiting-time constraints.

Method:

- MILP / lexicographic goal programming.
- Solves full model where possible.
- Uses Line Splitting Algorithm to decompose large real instances: construct one new line at a time, update budget and demand.

Usefulness for us:

- Good reference for evaluating passenger travel time and operator cost.
- Too complex for our core reproduction unless we simplify frequency away.

## Medium-Relevance Papers

### Lammaihri et al. 2024, Optimizing Metro Station Locations and Line Layouts in Selangor

Local file: `lammaihri_2024_selangor.pdf`

Actual city/case: Selangor, Malaysia.

Input data:

- GeoJSON administrative boundaries.
- Population density from DOSM.
- Demand generators: business districts, residential areas, airports, schools, major hubs.
- Predefined number of stations and lines estimated by benchmarking against population.

Objective:

- Stage 1: station placement maximizes population served and proximity to demand generators.
- Stage 2: line layout minimizes travel times and keeps network coherent.

Constraints:

- Geographic boundaries.
- Implicit geographic constraints.
- Fixed number of stations and lines.

Method:

- Two-stage GA.
- Population of station-coordinate sets and line layouts.
- Roulette selection, crossover, mutation, elitism.
- Line mutations include station swaps, subsequence reversal, station transfer/removal/addition.

Usefulness:

- Good simple GA template, but less rigorous than Chai/Ahmed/Laporte.

### Kumar 2024, Modified ACO for Metro Route Planning in Chennai

Local file: `kumar_2024_aco_metro.pdf`

Actual city/case: Chennai, India.

Input data:

- Chennai GIS/geographical data.
- Land-use data.
- Census data.
- POIs.
- Origin and destination pairs, e.g. Chennai Airport to Thiruvottriyur; Tambaram to Sholingnallur.

Objective:

- Select efficient metro route among generated alternatives.
- Factors include accessibility, cost, travel time, population/POI coverage.

Constraints:

- Rule-based radius/conditions for station/route candidates.
- Not fully formalized in the extracted text.

Method:

- Modified ant colony optimization.
- Compare generated route with existing Chennai metro route.

Usefulness:

- Good case inspiration, but paper is less precise mathematically than the stronger OR papers.

### Silva et al. 2022, Multi-Objective Optimization for Transit Network Design

Local file: `silva_2022_tndp.pdf`

Actual city/case: Lisbon, Portugal.

Input data:

- Road network.
- Existing Lisbon metro and bus network.
- Smart-card AFC validations from Carris buses/trams and Metro stations.
- OD demand inferred from smart-card data.
- Lisbon divided into 30 x 30 grid OD units.

Objective:

- Minimize unsatisfied demand.
- Minimize in-vehicle time.
- Minimize transfer needs.
- Minimize waiting/walking time and operator/environmental costs.
- Uses multi-objective Pareto search, then learns weights for a single-objective formulation from ratings.

Constraints:

- Route pool constraints.
- Maximum occupancy/safety capacity mentioned.
- Practical route structure constraints.

Method:

- Genetic algorithms: NSGA-II and classic GA.
- Route generation from road network.
- Evaluate multimodal trips and network objectives.

Usefulness:

- Good for OD-from-smart-card idea and multi-objective evaluation.
- More bus-network redesign than rail line alignment.

### Lu et al. 2025, Line Planning Under Crowding

Local file: `lu_2025_line_planning.pdf`

Actual city/case: Swiss nationwide public transit network subset, plus 5x5 benchmark.

Input data:

- Public transit network.
- Predefined line pool.
- OD demand.
- Vehicle capacity/crowding factor.
- Budget and line frequency bounds.

Objective:

- Minimize total perceived passenger travel time, including crowding penalty.

Constraints:

- Select lines from predefined pool under budget.
- Every edge must have at least one operated line.
- Line frequency lower/upper bounds.
- All passengers must be routed.
- Capacity/crowding represented as soft penalty via SOCP reformulation.

Method:

- MI-SOCP formulation.
- Cutting planes and column generation.

Usefulness:

- Useful for later operational evaluation of crowding, but not for initial line alignment.

### Lu et al. 2023/2024, ODMTS with Congestion and Dedicated Bus Lanes

Local file: `lu_2023_odmts.pdf`

Actual city/case: Metro Atlanta, Georgia, with Gwinnett County to Atlanta I-85 corridor.

Input data:

- Directed multimodal graph with shuttle, bus, and rail arcs.
- Existing rail network and potential bus arcs/frequencies.
- OD passenger trips, including existing and latent demand.
- POLARIS travel-time basis between TAZs.
- Google Directions API congestion scaling factors.
- Fare, travel time, waiting time, bus/shuttle costs.

Objective:

- Minimize convex combination of system cost and passenger inconvenience.
- Include ticket revenue/adoption benefit.

Constraints:

- Bus frequency balance at hubs.
- At most one frequency selected among parallel bus arcs.
- Transfer limit.
- Passenger path flow conservation.
- Bus arcs usable only if opened.

Method:

- Bilevel optimization with passenger adoption.
- Benders decomposition for fixed-demand design.
- Iterative heuristic for large-scale latent demand.

Usefulness:

- Good passenger assignment/adoption model; less relevant to rail alignment because it designs multimodal bus/shuttle services.

### Lonardi et al. 2021, Multicommodity Routing Optimization for Engineering Networks

Local file: `lonardi_2021_multicommodity.pdf`

Actual city/case: Paris metro.

Input data:

- Paris metro topology with 302 nodes and 359 edges.
- Edge lengths.
- Passenger flow/commodity assumptions by station.

Objective:

- Optimize passenger flow routing through a shared infrastructure.
- Study trade-off between energy dissipation and infrastructure cost.

Constraints:

- Flow conservation / Kirchhoff-law formulation.
- Shared edge capacities/conductivities.

Method:

- Optimal transport / multicommodity routing dynamics.
- Compare with Dijkstra shortest paths.
- Resilience analysis under node/edge removal.

Usefulness:

- Useful for evaluating passenger assignment and bottlenecks on a fixed network.
- Not a line-planning paper.

### Darvariu et al. 2021/2022, Planning Spatial Networks with Monte Carlo Tree Search

Local file: `darvariu_2021_mcts_networks.pdf`

Actual city/case: real-world metro networks and internet backbone networks.

Input data:

- Starting spatial graph with node coordinates.
- Candidate edge additions.
- Edge costs based on spatial length.
- Budget proportional to original network edge cost.

Objective:

- Add edges to maximize global efficiency or robustness.

Constraints:

- Edge-addition budget.
- Spatial edge cost.
- Connectivity/allowed action restrictions.

Method:

- Deterministic MDP.
- Monte Carlo Tree Search / UCT with spatial graph improvements.
- Baselines: random, greedy, min-cost, spectral heuristics.

Usefulness:

- Good conceptual fit if we model "add a connector line/edge to existing metro network."
- It optimizes abstract network metrics rather than OD demand or buildable line alignment.

### Sani 2022, Potential Demand Model for Feeder Network Design

Local file: `sani_2022_feeder.pdf`

Actual city/case: Tehran district 10.

Input data:

- Road graph with links and nodes.
- PTN stations.
- Traffic demand per link.
- Walking distance to links.
- Distance from links to PTN stations.
- Demand for reaching each PTN station.
- Population density, average age, housing density.

Objective:

- Maximize potential demand served by feeder routes.

Constraints:

- Each feeder route allocated to a main station.
- Circular route connectivity.
- Maximum number of feeder lines.
- Maximum travel time/distance.
- Node labelling constraints.

Method:

- NLP transformed toward LP.
- Heuristic/metaheuristic route generation.

Usefulness:

- Good for feeder/access modeling, but not rail line alignment.

## Low-Relevance Papers

### Batik et al. 2022, Shape-Guided Mixed Metro Map Layout

Local file: `batik_2022_metro_map.pdf`

This is about schematic metro map drawing, not physical line planning. It optimizes visual constraints such as guide-shape matching, spacing, angular resolution, and geographic preservation. Useful only if we later need visualization.

### Chen 2025, Unified Crew Planning and Replanning Optimization in Multi-Line Metro Systems

Local file: `chen_2025_crew_planning.pdf`

This is crew planning/replanning for Shanghai and Beijing metro operations. Inputs include train tasks, crew qualifications, depots, transfer stations, disruptions, and preferences. Objective is crew operation cost and urgent task completion. Not relevant to line alignment except as an example of time-space network modeling.

### Ng 2024, Transit Pattern / Service Pattern Optimization

Local file: `ng_2024_transit_pattern.pdf`

This appears to focus on train service patterns / stopping patterns rather than physical line planning. It is useful only if we later evaluate operation patterns on an already chosen line.

### s41062-025-02356-5, Half-Century TNDP Review

Local file: `s41062-025-02356-5.pdf`

This is a broad review of Transit Route Network Design Problem and Frequency Setting Problem. It is useful for proposal background and taxonomy, but not a reproduction target.

## Recommended Project Formulation

Title candidate:

> Budget-Constrained Urban Rail Line Planning on an Existing Metro Network Using OD Demand Capture

Problem:

Given:

- existing metro stations and lines;
- candidate station nodes from POIs, population/job centers, road intersections, and existing stations;
- candidate edges from road corridors or spatial k-nearest-neighbor graph;
- OD demand matrix between traffic zones;
- node rewards from population/jobs/POIs and OD endpoints;
- edge construction cost from distance and corridor penalties;
- transfer penalty and travel-time model.

Find:

- one new line/path, or one local extension/connector, under length/cost budget.

Maximize:

- OD demand captured or travel-time reduction;
- population/job coverage within station buffer;
- transfer/connectivity improvement.

Subject to:

- maximum line length or construction budget;
- station spacing min/max;
- line must be a simple path;
- must connect to at least one/two existing metro stations;
- optional: angle/smoothness constraints;
- optional: avoid forbidden areas or high-cost land-use classes.

## Suggested Algorithms for Reproduction

Baseline algorithms:

1. Population greedy: choose path through high population/job nodes.
2. OD direct baseline: connect the largest OD pair by shortest path.
3. Shortest path baseline: connect two selected terminals by minimum cost.
4. Existing-network baseline: no new line.

Main algorithm candidates:

1. Greedy insertion heuristic from Laporte et al.:
   - start with highest OD-benefit station pair;
   - insert stations that maximize marginal captured demand per added length.

2. k-shortest path + budgeted maximum coverage:
   - generate candidate paths between high-demand terminals;
   - score each path by covered OD/pop/jobs;
   - select best path under budget.

3. Prize-collecting path heuristic:
   - nodes have prizes from population/jobs/OD endpoint demand;
   - edges have cost;
   - use greedy extension, local search, or dynamic programming on simplified DAG.

4. Simulated annealing neighborhood search:
   - state is a path or line;
   - moves: add station, remove station, swap segment, reconnect by shortest path;
   - objective combines demand benefit and cost.

5. Lightweight GA:
   - chromosome is station sequence;
   - repair infeasible station spacing and budget;
   - fitness is weighted demand capture minus cost.

For CS240, I recommend using (1) and (2) as main methods because they are classical, explainable, and reproducible without a huge metaheuristic tuning burden.

## Data Plan for Our Project

Required minimum data:

- Existing metro network: station coordinates and line edges.
- Candidate stations:
  - existing stations,
  - high population cells,
  - high job/POI cells,
  - major road intersections if available.
- Population proxy:
  - gridded population if available, or AMap/POI residential density proxy.
- Jobs proxy:
  - office/commercial/industrial POIs, land-use categories, or employment center proxies.
- OD matrix:
  - gravity model from population to jobs if real OD is unavailable.
  - OD_ij proportional to population_i * jobs_j / distance_ij^alpha.

Evaluation:

- Coverage: population/jobs within 800 m or 1 km station buffer.
- OD capture: OD pairs whose origin and destination are close to selected stations and whose old+new travel time improves.
- Travel time: shortest path on existing network vs. expanded network with transfer penalty.
- Transfer efficiency: average transfer count or transfer penalty over OD sample.
- Cost: line length plus penalties for difficult land-use segments.
- Scalability: runtime as candidate nodes and OD pairs increase.

## Best Target for a Course Proposal

I would not make GA the center. GA is common in the literature, but for an algorithms course we can make the classical structure clearer:

> We formulate urban rail line planning as a budgeted prize-collecting path / OD maximum coverage problem on a spatial graph. We implement greedy extension and greedy insertion heuristics inspired by rapid transit alignment planning, compare them with shortest-path and population-greedy baselines, and evaluate on a real city using existing metro lines, population/job proxies, and a gravity-model OD matrix.

This stays faithful to the course requirement: classical algorithms solving modern urban data application problems.

## External Sources Used

- Chai et al. 2019 MDPI page: https://www.mdpi.com/2071-1050/11/21/6128
- Laporte et al. 2005 PDF: https://idus.us.es/bitstreams/0fd59127-f863-4d79-88b8-a7f157e84d11/download
- Gutierrez-Jarpa et al. 2013 ScienceDirect record: https://www.sciencedirect.com/science/article/pii/S0305054813001706
- Gutierrez-Jarpa et al. 2015 ATMOS PDF: https://drops.dagstuhl.de/storage/01oasics/oasics-vol048_atmos2015/OASIcs.ATMOS.2015.95/OASIcs.ATMOS.2015.95.pdf
- He et al. 2024 Springer open-access article: https://link.springer.com/article/10.1007/s40864-024-00212-w
- Yin & Peng 2023 MDPI page: https://www.mdpi.com/2227-7390/11/6/1539
- Wang et al. 2025 Chongqing TOD/route design page: https://www.mdpi.com/2227-7390/13/16/2558
