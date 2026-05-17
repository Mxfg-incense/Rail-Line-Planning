"""Shared configuration for the line-network model folder."""

from __future__ import annotations

from pathlib import Path


HERE = Path(__file__).resolve().parent
PIPELINE_DIR = HERE.parent
WORKSPACE_DIR = PIPELINE_DIR.parent

OUTPUT_DIR = HERE / "output"
DEMAND_DIR = OUTPUT_DIR / "demand_data"
CACHE_DIR = WORKSPACE_DIR / "cache" / "line_network_model"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DEMAND_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Edit this one bbox when switching study areas. Format: west, south, east, north.
# BBOX = (118.49, 24.77, 118.70, 24.99)
BBOX = (121.40, 31.14, 121.56, 31.26) 
TOTAL_POPULATION = 3_000_000

# Candidate-station center generation.
POPULATION_CENTER_COUNT = 29
JOB_CENTER_COUNT = 18
CENTER_MERGE_RADIUS_M = 1_000.0

# Fixed values for non-clustered special centers.
TRANSPORT_HUB_VALUES = {
    "rail_hub": 260_000.0,
    "airport_hub": 300_000.0,
}
TRANSPORT_CENTER_NAME_ALIASES = {
    "上海站": "上海站",
    "上海火车站": "上海站",
    "上海南站": "上海南站",
    "龙阳路": "龙阳路",
    "龙阳路地铁站": "龙阳路",
    "磁浮龙阳路站": "龙阳路",
}
TRANSPORT_CENTER_FIXED_VALUES = {
    "上海站": 300_000.0,
    "上海南站": 260_000.0,
    "龙阳路": 220_000.0,
}
TRANSPORT_CENTERS_REQUIRED = False
COMMERCIAL_CENTER_VALUE = 30_000.0

# Alignment penalty for planned lines/networks.
# A turn angle is measured as 0 degrees for a straight continuation and larger
# values for sharper bends. Only turns above the threshold are penalized.
TURN_PENALTY_THRESHOLD_DEG = 60.0
TURN_PENALTY_WEIGHT = 120_000_000.0
MAX_TURN_ANGLE_DEG = 135.0
