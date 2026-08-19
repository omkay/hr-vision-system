"""Model wrappers (face, ReID, detector) with lazy singletons.

Models are heavy to load (~seconds to tens of seconds). The service loads each
one once on first use and reuses it across requests.
"""
from __future__ import annotations

import contextlib
import threading
from typing import List, Optional

import cv2
import numpy as np

from .config import DEVICE, YOLO_WEIGHTS, DEFAULT_BEHAVIOR_OBJ_CONF

_lock = threading.Lock()
_face_emb = None
_reid_emb = None
_detector = None


def _onnx_providers_for(device: str) -> list:
    """InsightFace runs on onnxruntime, not torch — torch device strings
    (cuda/mps/cpu) don't apply to it directly, so this maps DEVICE to the
    matching onnxruntime execution provider, falling back to CPU if the
    installed onnxruntime build doesn't actually have that provider
    compiled in (e.g. a CPU-only wheel) rather than raising at model-load
    time.
    """
    import onnxruntime as ort
    available = ort.get_available_providers()

    wanted = {
        "cuda": "CUDAExecutionProvider",
        # CoreML EP — Apple Neural Engine / GPU via Core ML, only relevant
        # when this process runs natively on macOS (see config.py's DEVICE
        # comment). Standard onnxruntime wheels for macOS bundle this, but
        # it's not guaranteed for every build, hence the availability check.
        "mps": "CoreMLExecutionProvider",
    }.get(device)

    if wanted and wanted in available:
        return [wanted, "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


class FaceEmbedder:
    def __init__(self, device: str = DEVICE):
        from insightface.app import FaceAnalysis
        providers = _onnx_providers_for(device)
        self.app = FaceAnalysis(name="buffalo_l", providers=providers)
        self.app.prepare(ctx_id=0 if device == "cuda" else -1, det_size=(640, 640))

    def embed(self, bgr_image: np.ndarray) -> Optional[np.ndarray]:
        faces = self.app.get(bgr_image)
        if not faces:
            return None
        f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
        return f.normed_embedding.astype(np.float32)


class ReIDEmbedder:
    def __init__(self, device: str = DEVICE):
        import torch
        import torchreid
        import torchvision.transforms as T
        self.torch = torch
        self.device = device
        self.model = torchreid.models.build_model(
            name="osnet_x1_0", num_classes=1000, pretrained=True
        ).to(device).eval()
        self.tf = T.Compose([
            T.ToPILImage(), T.Resize((256, 128)), T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    def embed(self, bgr_crop: np.ndarray) -> np.ndarray:
        with self.torch.inference_mode():
            rgb = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2RGB)
            x = self.tf(rgb).unsqueeze(0).to(self.device)
            v = self.model(x).squeeze(0).cpu().numpy()
        return (v / (np.linalg.norm(v) + 1e-9)).astype(np.float32)

    def embed_batch(self, bgr_crops: List[np.ndarray]) -> np.ndarray:
        if not bgr_crops:
            return np.zeros((0, 512), np.float32)
        with self.torch.inference_mode():
            xs = self.torch.stack([
                self.tf(cv2.cvtColor(c, cv2.COLOR_BGR2RGB)) for c in bgr_crops
            ]).to(self.device)
            v = self.model(xs).cpu().numpy()
        v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
        return v.astype(np.float32)


@contextlib.contextmanager
def _allow_legacy_torch_load():
    import torch
    orig = torch.load
    def _loader(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return orig(*args, **kwargs)
    torch.load = _loader
    try:
        yield
    finally:
        torch.load = orig


TARGET_CLASSES = {0: "person", 67: "cell phone", 63: "laptop", 62: "monitor"}

# Stock COCO classes that are easily confused with domain-specific background
# objects (a dish rack read as "cell phone", a cleaning robot read as
# "monitor") — these need a stricter confidence bar than "person" before
# they're trusted at all. YOLO's own conf= param applies one threshold to
# every class in a single .track() call, so this is enforced as a post-filter
# below rather than by re-running detection per class.
BEHAVIOR_CLASSES = {"cell phone", "laptop", "monitor"}


class PersonObjectDetector:
    def __init__(self, weights: str = YOLO_WEIGHTS, device: str = DEVICE,
                 conf: float = 0.30, iou: float = 0.50,
                 behavior_conf: float = DEFAULT_BEHAVIOR_OBJ_CONF):
        from ultralytics import YOLO
        with _allow_legacy_torch_load():
            self.model = YOLO(weights)
        self.device = device
        self.conf = conf
        self.iou = iou
        self.behavior_conf = behavior_conf

    def reset_tracker(self):
        """Clear ByteTrack state so the next video starts with a clean slate.

        Calling tracker.reset() clears tracked/lost/removed stracks, resets
        the frame counter and Kalman filter, and resets the track-ID counter —
        all while leaving predictor.trackers intact so ultralytics' persist=True
        path continues to work correctly on subsequent frames.
        """
        predictor = getattr(self.model, "predictor", None)
        if predictor is not None:
            for tracker in getattr(predictor, "trackers", None) or []:
                tracker.reset()

    def track(self, frame_bgr, conf=None, iou=None, behavior_conf=None):
        # conf= here is the LOW bar (0.30 by default) so "person" detections
        # aren't missed — ultralytics applies one threshold to the whole
        # frame in a single .track() call. BEHAVIOR_CLASSES are then held to
        # a stricter behavior_conf below, post-hoc, since a false "cell
        # phone"/"monitor" reading is much more visually plausible on random
        # background objects than a false "person" is.
        behavior_conf = behavior_conf if behavior_conf is not None else self.behavior_conf
        res = self.model.track(
            frame_bgr, persist=True,
            conf=conf if conf is not None else self.conf,
            iou=iou if iou is not None else self.iou,
            tracker="bytetrack.yaml",
            device=self.device,
            verbose=False,
        )[0]
        out = []
        if res.boxes is None or res.boxes.id is None:
            return out
        xyxy = res.boxes.xyxy.cpu().numpy().astype(int)
        conf_ = res.boxes.conf.cpu().numpy()
        cls = res.boxes.cls.cpu().numpy().astype(int)
        ids = res.boxes.id.cpu().numpy().astype(int)
        for b, c, k, tid in zip(xyxy, conf_, cls, ids):
            if k not in TARGET_CLASSES:
                continue
            cls_name = TARGET_CLASSES[int(k)]
            if cls_name in BEHAVIOR_CLASSES and float(c) < behavior_conf:
                continue
            out.append(dict(bbox=b, conf=float(c), cls_id=int(k),
                            cls_name=cls_name, track_id=int(tid)))
        return out


def get_face_embedder() -> FaceEmbedder:
    global _face_emb
    with _lock:
        if _face_emb is None:
            _face_emb = FaceEmbedder()
    return _face_emb


def get_reid_embedder() -> ReIDEmbedder:
    global _reid_emb
    with _lock:
        if _reid_emb is None:
            _reid_emb = ReIDEmbedder()
    return _reid_emb


def get_detector(conf: float = 0.30, iou: float = 0.50) -> PersonObjectDetector:
    """Returns the cached detector. Conf/iou can be overridden per-call via .track()."""
    global _detector
    with _lock:
        if _detector is None:
            _detector = PersonObjectDetector(conf=conf, iou=iou)
    return _detector
