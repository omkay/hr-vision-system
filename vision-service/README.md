# Employee Activity Tracking — research notebook

> **REST service documentation** → see [SERVICE.md](SERVICE.md)

End-to-end computer-vision pipeline that watches office surveillance video and produces
structured activity events per employee (presence at desk, phone usage, colleague
interactions).

## Pipeline at a glance

```
YOLOv8 (person + phone + laptop)
     │
     ▼
ByteTrack  ──► track_id (short-term)
     │
     ▼
InsightFace (face embedding)  +  OSNet / TorchReID (appearance embedding)
     │
     ▼
IdentityFuser (temporal voting over gallery matches) ──► employee_id (long-term)
     │
     ▼
Event engine (zones / phone / proximity state-machines) ──► events.csv
     │
     ▼
Annotated MP4  +  timeline plots  +  interaction graph
```

## Quick start (recommended)

**Python 3.11 is required** on both Mac and Windows.

### macOS / Linux

```bash
# CPU / Apple Silicon (MPS)
python3.11 setup.py

# NVIDIA GPU (CUDA 12.4)
python3.11 setup.py --cuda
```

### Windows

Make sure [Python 3.11](https://www.python.org/downloads/release/python-3119/) is
installed and `py` launcher is available (the default installer sets this up).

```bat
REM CPU only
py -3.11 setup.py

REM NVIDIA GPU (CUDA 12.4)
py -3.11 setup.py --cuda
```

`setup.py` creates a `venv/` folder, installs all dependencies in the correct order,
and prints the command to launch the notebook when it finishes.

---

## Running the marimo notebook

Marimo is a reactive notebook — cells re-run automatically when their inputs change.

### macOS / Linux

```bash
# Interactive editor (recommended for development)
venv/bin/marimo edit employee_activity_tracking_marimo.py

# Read-only app view (good for demos)
venv/bin/marimo run employee_activity_tracking_marimo.py
```

### Windows

```bat
REM Interactive editor
venv\Scripts\marimo edit employee_activity_tracking_marimo.py

REM Read-only app view
venv\Scripts\marimo run employee_activity_tracking_marimo.py
```

Marimo opens in your browser automatically at `http://localhost:2718`.

### Marimo workflow

| Section | What to do |
|---------|-----------|
| 1 — Config | Review thresholds and paths (defaults usually work) |
| 2 — Gallery | Drop photos in `gallery/<name>/face/` and `gallery/<name>/body/`, then click **Build / refresh gallery** |
| 3 — Zone drawer | Select a video, click **Open Zone Drawer**, draw rectangles over the frame, copy the JSON and paste it back into the notebook |
| 4 — Pipeline | Pick a video, set a `max_frames` limit for a smoke test, click **▶ Run** |
| 5+ — Results | Annotated MP4, Gantt timeline, per-employee totals, and interaction graph update reactively |

---

## Folder layout

```
graduation project/
├── employee_activity_tracking_marimo.py   # reactive marimo notebook
├── employee_activity_tracking.ipynb       # classic Jupyter notebook
├── zone_drawer_template.html              # zone drawing UI (generated at runtime)
├── setup.py                               # cross-platform environment setup
├── requirements.txt                       # pinned dependencies (reference)
├── data/                                  # input videos (.mp4) — not committed
├── gallery/                               # enrolled employees
│   ├── alice/
│   │   ├── face/   alice_01.jpg …
│   │   └── body/   alice_body_01.jpg …
│   └── bob/ …
├── models/                                # cached model weights — not committed
└── outputs/                               # annotated videos + events CSV/JSON — not committed
```

---

## Windows setup — step by step

1. **Install Python 3.11**
   Download from https://www.python.org/downloads/release/python-3119/ — tick
   **"Add Python to PATH"** and **"Use admin privileges"** during install.

2. **Install Git for Windows** (if not already)
   https://git-scm.com/download/win — use default settings.

3. **Clone the repo**
   ```bat
   git clone git@github.com:omkay/employee_activity_tracker_2026.git
   cd employee_activity_tracker_2026
   ```

4. **Run setup**
   ```bat
   py -3.11 setup.py
   ```
   This takes 5–10 minutes on first run (downloads ~2 GB of wheels).

5. **Launch the notebook**
   ```bat
   venv\Scripts\marimo edit employee_activity_tracking_marimo.py
   ```

6. **Place your videos** in the `data/` folder (create it if it doesn't exist).

### Windows GPU (optional)

If you have an NVIDIA GPU, use `py -3.11 setup.py --cuda` in step 4 instead.
Requires CUDA 12.4 drivers — check with `nvidia-smi`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `py` not found on Windows | Re-install Python 3.11 and tick "Use Python Launcher" |
| `torchreid` install errors on Windows | Ensure Visual C++ Build Tools are installed (https://aka.ms/vs/17/release/vs_buildtools.exe) |
| `onnxruntime` import fails on Apple Silicon | Run `setup.py` again — it installs the correct `onnxruntime==1.25.1` for NumPy 2.x |
| `insightface` model download fails | It auto-downloads to `~/.insightface`; pre-fetch the `buffalo_l` bundle manually |
| `yolov8m.pt` download stalls | Pre-download from [ultralytics releases](https://github.com/ultralytics/assets/releases) and drop into `models/` |
| Ultra-slow on CPU | Set `FRAME_STRIDE=3–5` and lower `max_frames`; also consider `yolov8n.pt` |
| Zone drawer image doesn't load | Make sure a video is selected and the `outputs/` folder exists |

---

## Stack

- **Detection & tracking**: PyTorch, Ultralytics YOLOv8, ByteTrack (built-in)
- **Face recognition**: InsightFace (buffalo_l / ArcFace)
- **Re-identification**: TorchReID (OSNet x1_0)
- **Zone geometry**: shapely
- **Visualisation**: matplotlib, seaborn
- **Notebook**: marimo (reactive)

## Notes & caveats

- All thresholds live in the **Configuration** cell (Section 1). Tune them for your
  camera geometry.
- Zones are configured interactively in Section 3 — draw them over a real frame before
  running on footage.
- This is a research prototype. Deploying it in a real workplace requires written
  notice to employees, consent where legally required, short retention windows, and
  strict access control.
