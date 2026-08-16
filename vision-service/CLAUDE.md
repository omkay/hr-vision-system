# CLAUDE.md

## Project Overview

Employee Activity Tracking is a research CV pipeline that processes office videos and
produces structured events (desk presence, phone usage, colleague interactions).

Main entry points:
- `employee_activity_tracking_marimo.py` (reactive marimo notebook, primary workflow)
- `employee_activity_tracking.ipynb` (classic Jupyter version)

## Environment

- Python `3.11` is required.
- Preferred setup command:
  - macOS/Linux: `python3.11 setup.py`
  - Windows: `py -3.11 setup.py`
- CUDA setup (optional): add `--cuda`.
- `setup.py` creates `venv/` and installs dependencies in the expected order.

## Run Commands

Marimo (recommended):
- Edit mode: `venv/bin/marimo edit employee_activity_tracking_marimo.py`
- App mode: `venv/bin/marimo run employee_activity_tracking_marimo.py`

Windows equivalents:
- `venv\Scripts\marimo edit employee_activity_tracking_marimo.py`
- `venv\Scripts\marimo run employee_activity_tracking_marimo.py`

Optional `uv` workflow:
- `uv run marimo edit employee_activity_tracking_marimo.py`

## Repo Layout

- `data/` input videos
- `gallery/` enrolled employees (`face/` and `body/`)
- `models/` model weights cache
- `outputs/` annotated videos and event artifacts
- `zone_drawer_template.html` helper UI for drawing zones

## Working Conventions

- Keep this as a research/prototype codebase; avoid over-engineering.
- Prefer minimal, targeted changes and preserve existing behavior unless asked.
- If changing thresholds or pipeline defaults, mention rationale in commit/PR notes.
- Avoid committing large generated artifacts from `outputs/`, `models/`, or `runs/`.

## Typical Workflow

1. Place videos in `data/`.
2. Add gallery photos in `gallery/<name>/face/` and `gallery/<name>/body/`.
3. Open marimo in edit mode.
4. Build/refresh gallery.
5. Draw zones and paste JSON into the notebook.
6. Run a smoke test with limited `max_frames`, then full run.
7. Review outputs and events in `outputs/`.

## Troubleshooting Quick Notes

- If marimo or imports fail, re-run `setup.py` with Python 3.11.
- For Apple Silicon ONNX issues, use the pinned dependency set from setup.
- For slow CPU runs, increase frame stride and lower `max_frames`.
