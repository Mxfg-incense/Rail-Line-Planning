# Formal Problem Specification

## Sets

- `R`: residential demand points from OSM/OpenHouse population estimation.
- `V`: candidate metro stations.
- `H`: mandatory or strongly preferred hub stations, `H subset V`.
- `E`: feasible links between candidate stations.
- `K`: output rail lines. In the first baseline, `|K| = 1`.

## Input data

For each residential point `r in R`:

- coordinate `(lon_r, lat_r)`
- population `p_r`

For each station candidate `i in V`:

- coordinate `(lon_i, lat_i)`
- covered population:

```text
P_i = sum p_r for r within walk_radius of i
```

For each station pair `(i,j)`:

- OD demand:

```text
q_ij = 1
```

- construction distance:

```text
c_ij = geographic_distance(i, j)
```

## Decision variable

Baseline path version:

```text
P = ordered subset of V
```

Later network version:

```text
x_ij = 1 if link (i,j) is built
y_i  = 1 if station i is included
```

## Objective

For the current simplified project model:

```text
maximize score(P) = captured_OD(P) - alpha * length(P)
```

where:

```text
captured_OD(P) = sum q_ij for all i,j included in P
length(P)      = sum c_ij over adjacent station pairs in P
```

Since `q_ij = 1`, the OD part is simply the number of connected station pairs
inside the selected line:

```text
captured_OD(P) = |P| * (|P| - 1) / 2
```

## Constraints

- Total length:

```text
length(P) <= B
```

- Adjacent station spacing:

```text
s_min <= c_(v_t,v_{t+1}) <= s_max
```

- No repeated stations:

```text
v_t != v_u for t != u
```

- Hub preference:

```text
include required hubs when feasible
```

In the baseline script this is handled heuristically by starting from a hub and
giving hubs priority in candidate generation. It can be hardened into an exact
constraint later.

## What is deliberately ignored in v1

- train frequency and capacity;
- detailed passenger assignment;
- transfer penalty;
- construction engineering feasibility beyond distance;
- non-uniform OD;
- exact road-network alignment.

Those can be added after the base combinatorial problem is stable.
