"""Station data loading and toy station generation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_STATION_CSV = Path(__file__).resolve().parent / "output" / "01_candidate_stations_table.csv"


def _project_lon_lat(lon: pd.Series, lat: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Project lon/lat to local meter-like coordinates with an equirectangular approximation."""
    lon0 = float(lon.mean())
    lat0 = float(lat.mean())
    x = (lon - lon0) * 111_320.0 * np.cos(np.deg2rad(lat0))
    y = (lat - lat0) * 110_540.0
    return x, y


def normalize_station_table(stations: pd.DataFrame) -> pd.DataFrame:
    """Return a station table with canonical columns: station_id, x, y, population_value, total_value."""
    df = stations.copy()
    if "station_id" not in df.columns:
        df["station_id"] = [f"S{i + 1:03d}" for i in range(len(df))]

    if {"x", "y"}.issubset(df.columns):
        df["x"] = pd.to_numeric(df["x"], errors="coerce")
        df["y"] = pd.to_numeric(df["y"], errors="coerce")
    elif {"lon", "lat"}.issubset(df.columns):
        df["x"], df["y"] = _project_lon_lat(
            pd.to_numeric(df["lon"], errors="coerce"),
            pd.to_numeric(df["lat"], errors="coerce"),
        )
    else:
        raise ValueError("stations must contain either x/y or lon/lat columns")

    if "population_value" not in df.columns:
        if "total_value" in df.columns:
            df["population_value"] = df["total_value"]
        else:
            raise ValueError("stations must contain population_value or total_value")
    if "total_value" not in df.columns:
        df["total_value"] = df["population_value"]

    for col in ["population_value", "total_value"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).clip(lower=0.0)

    df["station_id"] = df["station_id"].astype(str)
    df = df.dropna(subset=["x", "y"]).reset_index(drop=True)
    return df


def load_stations(path: str | Path = DEFAULT_STATION_CSV) -> pd.DataFrame:
    """Load candidate stations from CSV and normalize expected columns."""
    return normalize_station_table(pd.read_csv(path))


def generate_toy_stations(n: int = 50, seed: int = 42) -> pd.DataFrame:
    """Generate clustered synthetic stations with demand values for a runnable toy example."""
    rng = np.random.default_rng(seed)
    centers = np.array([[0, 0], [8_000, 1_500], [-5_000, 6_000], [3_000, -7_000]])
    weights = np.array([0.34, 0.28, 0.22, 0.16])
    labels = rng.choice(len(centers), size=n, p=weights)
    coords = centers[labels] + rng.normal(0, 1_650, size=(n, 2))
    centrality = np.exp(-np.linalg.norm(coords, axis=1) / 9_000)
    population = rng.lognormal(mean=10.5, sigma=0.45, size=n) * (0.7 + centrality)
    total = population * rng.uniform(1.0, 2.2, size=n)
    return normalize_station_table(
        pd.DataFrame(
            {
                "station_id": [f"T{i + 1:03d}" for i in range(n)],
                "x": coords[:, 0],
                "y": coords[:, 1],
                "population_value": population,
                "total_value": total,
                "name": [f"Toy Station {i + 1}" for i in range(n)],
            }
        )
    )

