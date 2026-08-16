"""
Cross-platform setup for Employee Activity Tracking.

Usage
-----
Mac / Linux:
    python3.11 setup.py           # CPU / MPS
    python3.11 setup.py --cuda    # CUDA 12.4

Windows (Python 3.11 must be installed):
    py -3.11 setup.py             # CPU
    py -3.11 setup.py --cuda      # CUDA 12.4

After setup, run the notebook:
    Mac/Linux : venv/bin/marimo edit employee_activity_tracking_marimo.py
    Windows   : venv\\Scripts\\marimo edit employee_activity_tracking_marimo.py
"""

import subprocess
import sys
import platform
from pathlib import Path


def run(cmd):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    # ── guard: must be Python 3.11 ──────────────────────────────────────────
    if sys.version_info[:2] != (3, 11):
        sys.exit(
            f"ERROR: Python 3.11 required, got {sys.version_info.major}.{sys.version_info.minor}\n"
            "  Mac:     brew install python@3.11  (or pyenv install 3.11)\n"
            "  Windows: https://www.python.org/downloads/release/python-3119/"
        )

    use_cuda = "--cuda" in sys.argv
    is_win   = platform.system() == "Windows"
    root     = Path(__file__).parent
    venv     = root / "venv"
    pip      = venv / ("Scripts/pip.exe" if is_win else "bin/pip")
    python   = venv / ("Scripts/python.exe" if is_win else "bin/python")

    # ── 1. create venv ───────────────────────────────────────────────────────
    if not venv.exists():
        print("\n[1/6] Creating virtual environment…")
        run([sys.executable, "-m", "venv", venv])
    else:
        print("\n[1/6] Virtual environment already exists — skipping creation.")

    # ── 2. upgrade build tools ───────────────────────────────────────────────
    print("\n[2/6] Upgrading pip / setuptools / wheel…")
    run([pip, "install", "-q", "--upgrade", "pip", "setuptools", "wheel"])

    # ── 3. PyTorch ───────────────────────────────────────────────────────────
    print(f"\n[3/6] Installing PyTorch ({'CUDA 12.4' if use_cuda else 'CPU/MPS'})…")
    if use_cuda:
        run([pip, "install", "-q",
             "torch==2.11.0+cu124", "torchvision==0.26.0+cu124",
             "--index-url", "https://download.pytorch.org/whl/cu124"])
    elif is_win:
        # Windows CPU-only wheel
        run([pip, "install", "-q",
             "torch==2.11.0", "torchvision==0.26.0",
             "--index-url", "https://download.pytorch.org/whl/cpu"])
    else:
        # macOS: default PyPI wheel includes MPS support
        run([pip, "install", "-q", "torch==2.11.0", "torchvision==0.26.0"])

    # ── 4. main dependencies (PEP 517-friendly) ──────────────────────────────
    print("\n[4/6] Installing main dependencies…")
    run([pip, "install", "-q",
         "marimo==0.23.4",
         "ultralytics>=8.3.40,<8.4",
         "insightface==0.7.3",
         "onnxruntime==1.25.1",
         "lapx==0.5.11",
         "scikit-learn==1.5.0",
         "opencv-python>=4.10",
         "matplotlib==3.9.0",
         "seaborn==0.13.2",
         "pandas==2.2.2",
         "tqdm==4.66.4",
         "pyyaml==6.0.1",
         "shapely==2.0.4",
         "networkx>=3.2",
         "numpy>=2.0",
         ])

    # ── 5. legacy packages (setup.py imports pkg_resources at build time) ────
    print("\n[5/6] Installing legacy packages (no build isolation)…")
    run([pip, "install", "-q", "--no-build-isolation",
         "filterpy==1.4.5", "torchreid==0.2.5"])

    # ── 6. torchreid runtime deps ────────────────────────────────────────────
    print("\n[6/6] Installing torchreid runtime dependencies…")
    run([pip, "install", "-q",
         "gdown", "yacs", "h5py", "tensorboard",
         "future", "imageio", "Cython"])

    # ── done ─────────────────────────────────────────────────────────────────
    if is_win:
        run_cmd = r"venv\Scripts\marimo edit employee_activity_tracking_marimo.py"
    else:
        run_cmd = "venv/bin/marimo edit employee_activity_tracking_marimo.py"

    print(f"""
╔══════════════════════════════════════════════════════╗
║  Setup complete!                                     ║
║                                                      ║
║  Run the notebook:                                   ║
║    {run_cmd:<50} ║
╚══════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()
