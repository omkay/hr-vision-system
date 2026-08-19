"""Paths and default thresholds shared across the service."""
from __future__ import annotations

import os
from pathlib import Path

try:
    import torch
    if torch.cuda.is_available():
        DEVICE = "cuda"
    elif torch.backends.mps.is_available():
        # Apple Silicon GPU, via PyTorch's Metal Performance Shaders backend.
        # Only reachable when this process runs natively on the Mac — Docker
        # Desktop's Linux VM has no path to the host's Metal device, so the
        # containerized deployment always falls through to "cpu" here even
        # on an M-series Mac. See models.py for where this actually gets
        # used (ReIDEmbedder / PersonObjectDetector via torch/ultralytics)
        # and _onnx_providers_for() for the separate onnxruntime/CoreML path
        # InsightFace's FaceEmbedder uses instead (torch device strings
        # don't apply to onnxruntime sessions).
        DEVICE = "mps"
    else:
        DEVICE = "cpu"
except ImportError:
    DEVICE = "cpu"


def _resolve_project_dir() -> Path:
    env = os.environ.get("PROJECT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve().parent.parent
    if (here / "employee_activity_tracking_marimo.py").exists():
        return here
    return Path.cwd().resolve()


PROJECT_DIR = _resolve_project_dir()
DATA_DIR    = PROJECT_DIR / "data"
OUT_DIR     = PROJECT_DIR / "outputs"
GALLERY_DIR = PROJECT_DIR / "gallery"
MODELS_DIR  = PROJECT_DIR / "models"

for _d in (DATA_DIR, OUT_DIR, GALLERY_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

GALLERY_PATH = GALLERY_DIR / "gallery.npz"
ZONES_CONFIG_PATH = OUT_DIR / "zones_config.json"

# Default thresholds (mirror the marimo defaults).
DEFAULT_DET_CONF = 0.30
DEFAULT_DET_IOU  = 0.50
DEFAULT_FACE_THR = 0.45
DEFAULT_REID_THR = 0.75
DEFAULT_FUSE_WIN = 30
DEFAULT_STRIDE   = 2
DEFAULT_MAX_FRAMES = 600
DEFAULT_PROX_PX  = 180

# Cap detection resolution — our NVR footage runs up to 3840x2160, but YOLO's
# own preprocessing resizes to a much smaller imgsz internally regardless, so
# feeding it (and face/reid cropping) full 4K buys zero accuracy and directly
# caused a real 502 timeout on /checkin/video-multi (CPU-only inference on a
# multi-minute 4K clip, no early exit). Frames wider/taller than this on their
# longest side are downscaled once per frame before detection; zones and
# emitted bboxes stay self-consistent since everything downstream of the
# resize (zone matching, cropping, annotated-video output) operates in this
# same scaled coordinate space. Does NOT apply to checkin_video()'s
# motion/blur gates — those thresholds were GA-tuned against native-resolution
# footage (see GA optimisation/) and downscaling would need a separate re-tune.
DETECTION_MAX_DIM = 1280

# Annotated debug video (write_video=true on /events/run) writes only 1 out
# of every ANNOTATE_STRIDE *processed* frames to the output — a human
# reviewing footage to sanity-check tracking/identity doesn't need every
# frame, just enough temporal density to follow people moving between
# zones and confirm labels are stable. This is the single biggest lever on
# output file size (5x fewer frames written ≈ ~5x smaller before any
# codec-level compression), on top of the DETECTION_MAX_DIM downscale which
# already caps the frame resolution itself. The writer's output fps is
# computed to compensate (see pipeline.py), so playback speed still matches
# real elapsed time despite the skipped frames.
DEFAULT_ANNOTATE_STRIDE = 5

YOLO_WEIGHTS = str(PROJECT_DIR / "yolov8m.pt")
