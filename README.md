# CS240 Rail Planning Workspace

This workspace is managed with `uv`.

## Setup

```powershell
uv sync
```

## Run the OpenHousePopulator-style population pipeline

```powershell
uv run python .\transit_pipeline\openhouse_steps\run_all.py
```

Intermediate outputs are written to `transit_pipeline/output/openhouse_steps/`.

## Run the original full planning pipeline

```powershell
uv run python .\transit_pipeline\full_pipeline_v2.py
```
