# 2026-07-07 V1 Initial Implementation (Archive)

## Summary
- Added V1 simulation scaffold under `src/leakage_simulator/{...}`.
- Added fallback synthetic scene for missing CAD inputs.
- Added volume/face emitter support, gap sampling, material library, and relative brightness outputs.
- Added execution pipeline and output exporters (JSON/CSV/PNG).

## Implemented Files
- `src/leakage_simulator/{types.py,materials.py,geometry.py,synth.py,importers.py,gap.py,raytracer.py,engine.py,render.py,cli.py}`
- `docs/*`

## Baseline Parameters
- `ray_count`: 4000
- `max_depth`: 2
- `k_abs`: 0.12
- `k_brdf`: 1.0

## Outputs
- JSON: `outputs/run-*/<run_id>.json`
- CSV: `outputs/run-*/<run_id>_receiver.csv`
- PNG: `outputs/run-*/<run_id>_heatmap.png` (if matplotlib installed)
