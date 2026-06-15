"""Synthetic OD demand generation."""

from __future__ import annotations

import numpy as np
import pandas as pd


def station_distance_matrix(stations: pd.DataFrame, epsilon: float = 1e-6) -> np.ndarray:
    """Compute Euclidean station distance matrix."""
    coords = stations[["x", "y"]].to_numpy(float)
    diff = coords[:, None, :] - coords[None, :, :]
    return np.sqrt(np.sum(diff * diff, axis=2)) + epsilon


def generate_od_matrix(stations: pd.DataFrame, alpha: float = 1.5, normalize: bool = True) -> np.ndarray:
    """Generate an n x n gravity-model OD matrix from station population values and distances."""
    pop = stations["population_value"].to_numpy(float)
    dist = station_distance_matrix(stations)
    od = (pop[:, None] * pop[None, :]) / np.power(dist, alpha)
    np.fill_diagonal(od, 0.0)
    if normalize and od.sum() > 0:
        od = od / od.sum()
    return od

