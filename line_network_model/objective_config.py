"""Objective-function parameters for the line-network model."""

from __future__ import annotations


# Minimum geometric edge length retained in generated rail candidates.
MIN_EDGE_LENGTH_M = 1_000.0

# Objective:
#   connection reward
#   - lambda_1 * total construction length
#   - lambda_2 * turn-angle penalty
#   - lambda_3 * open endpoint count
CONSTRUCTION_COST_WEIGHT_PER_M = 20_000.0
TURN_PENALTY_WEIGHT = 170_000_000.0
ENDPOINT_PENALTY_WEIGHT = 100_000_000.0

# A turn angle is measured as 0 degrees for a straight continuation and larger
# values for sharper bends. Only turns above the threshold are penalized.
TURN_PENALTY_THRESHOLD_DEG = 75.0
