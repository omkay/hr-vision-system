"""Model wrappers (face, ReID, detector) with lazy singletons.

Models are heavy to load (~seconds to tens of seconds). The service loads each
one once on first use and reuses it across requests.
"""
from __future__ import annotations

import contextlib
import functools
import logging
import threading
from typing import List, Optional

import cv2
import numpy as np

from .config import (
    DEVICE, YOLO_WEIGHTS, DEFAULT_BEHAVIOR_OBJ_CONF, REID_MODEL_NAME,
    REID_WEIGHTS_PATH,
)

log = logging.getLogger(__name__)

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
    """OSNet body-appearance embedder.

    IMPORTANT: which weights are loaded dominates everything else about
    cross-camera identification. torchreid's `pretrained=True` gives OSNet's
    **ImageNet** weights, which are features for classifying objects, not for
    distinguishing people. Measured on this system's own footage with those
    weights, the correct employee scored 0.589 and the wrong one 0.579 — a
    0.01 margin, i.e. no discriminative power at all between two people.

    So a person-ReID checkpoint (Market1501 / MSMT17) is loaded from
    REID_WEIGHTS_PATH when present, and its absence is logged as a warning
    rather than passing silently, because "silently much worse" is the whole
    failure mode being fixed here.
    """

    def __init__(self, device: str = DEVICE):
        import torch
        import torchreid
        import torchvision.transforms as T
        self.torch = torch
        self.device = device
        has_reid_weights = REID_WEIGHTS_PATH.exists()
        # Fall back to the plain architecture when running without a
        # checkpoint: the *_ain_* variants only exist to be loaded with their
        # own trained weights, and torchreid has no ImageNet weights for
        # several of them.
        arch = REID_MODEL_NAME if has_reid_weights else "osnet_x1_0"
        self.model = torchreid.models.build_model(
            name=arch, num_classes=1000,
            # Skip the ImageNet download entirely when a real ReID checkpoint
            # is about to overwrite those weights anyway.
            pretrained=not has_reid_weights,
        )
        self.arch = arch
        if has_reid_weights:
            from torchreid.reid.utils import load_pretrained_weights
            load_pretrained_weights(self.model, str(REID_WEIGHTS_PATH))
            log.info("ReID: architecture %s with person-ReID weights from %s",
                     arch, REID_WEIGHTS_PATH)
        else:
            log.warning(
                "ReID: no person-ReID checkpoint at %s — falling back to OSNet's "
                "ImageNet weights, which are NOT trained to distinguish people. "
                "Cross-camera body matching will be close to useless (expect "
                "correct and incorrect employees to score within ~0.01 of each "
                "other). Fetch a domain-generalization checkpoint from "
                "torchreid's MODEL_ZOO into %s and restart — see "
                "config.REID_WEIGHTS_PATH for which one and why.",
                REID_WEIGHTS_PATH, REID_WEIGHTS_PATH,
            )
        self.model = self.model.to(device).eval()
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

        This MUST actually work, and the previous version silently didn't —
        with observable consequences: processing the same video twice in one
        long-lived service process gave different detections each time. Track
        IDs from earlier runs (241, 611) reappeared in later ones, whole
        people vanished from the output, and 401 person-frames on one run
        became 187 on the next. Anything measured across such runs — a
        threshold, a GA fitness score, "did this employee get checked in" —
        was comparing against a moving baseline.

        The mechanism: leftover `lost_stracks` from the previous video stay in
        the association pool and absorb new detections, and because
        `frame_id` never returns to 1, newly created tracks start with
        `is_activated=False` (see STrack.activate) and are excluded from
        `boxes.id` until a second frame associates to them. With a polluted
        pool many never activate at all, so the person is never reported.

        What this does:
          1. reset() each existing tracker — in the installed ultralytics this
             clears tracked/lost/removed stracks, sets frame_id back to 0,
             rebuilds the Kalman filter and calls reset_id(), which is
             everything needed;
          2. reset the global STrack ID counter directly as well, since it
             lives on BaseTrack rather than on any tracker instance and an
             upstream reset() that skipped it would silently reintroduce the
             leak.

        DO NOT delete `predictor.trackers` to "force" a rebuild. That was
        tried and it broke detection outright: ultralytics' Model.track() does
        `if not hasattr(self.predictor, "trackers"): register_tracker(...)`,
        so removing the attribute makes it re-register the tracker callbacks
        on every reset. The duplicated on_predict_postprocess_end then runs
        twice per frame, corrupting the association so that `boxes.id` comes
        back None and the pipeline reports ZERO person detections for the
        whole video — with the failure appearing only on the second and later
        videos of a job, as the duplicates accumulate. Confirmed against a
        camera whose people were detected fine before the change and not at
        all after.
        """
        predictor = getattr(self.model, "predictor", None)
        if predictor is not None:
            for tracker in getattr(predictor, "trackers", None) or []:
                if hasattr(tracker, "reset"):
                    tracker.reset()

        try:
            from ultralytics.trackers.basetrack import BaseTrack
            BaseTrack.reset_id()
        except Exception as e:  # pragma: no cover - upstream layout change
            log.warning("could not reset ByteTrack ID counter: %s", e)

    def detect(self, frame_bgr, conf=None, iou=None, behavior_conf=None):
        """Detect without tracking — for one-off images.

        `track()` mutates shared ByteTrack state, which is meaningless for a
        single unrelated still (there is no temporal continuity to exploit)
        and actively harmful: _checkin_bgr used to call track() per sampled
        frame, so a /checkin or /checkin/video request would seed the shared
        tracker with garbage associations that then corrupted the NEXT video
        processed by /events/run or /checkin/video-multi. Detections here get
        track_id=-1, which no caller of this method uses.
        """
        behavior_conf = behavior_conf if behavior_conf is not None else self.behavior_conf
        res = self.model.predict(
            frame_bgr,
            conf=conf if conf is not None else self.conf,
            iou=iou if iou is not None else self.iou,
            device=self.device,
            verbose=False,
        )[0]
        return self._collect(res, behavior_conf, track_ids=None)

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
        if res.boxes is None or res.boxes.id is None:
            return []
        return self._collect(res, behavior_conf,
                             track_ids=res.boxes.id.cpu().numpy().astype(int))

    def _collect(self, res, behavior_conf, track_ids=None):
        """Shared post-filter for detect()/track() results."""
        out = []
        if res.boxes is None or len(res.boxes) == 0:
            return out
        xyxy = res.boxes.xyxy.cpu().numpy().astype(int)
        conf_ = res.boxes.conf.cpu().numpy()
        cls = res.boxes.cls.cpu().numpy().astype(int)
        ids = track_ids if track_ids is not None else [-1] * len(cls)
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


_tracking_lock = threading.RLock()


def serialized_tracking(fn):
    """Decorator form of tracking_session() for whole-video functions.

    Applied to the pipeline entry points rather than wrapping their bodies in
    a `with` block, so the guarantee can't be lost to an early `raise` between
    acquiring the tracker and entering the try/finally — an unopenable video
    file would otherwise leave the lock held and deadlock every later request.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with tracking_session():
            return fn(*args, **kwargs)
    return wrapper


@contextlib.contextmanager
def tracking_session():
    """Exclusive, freshly-reset use of the shared detector's tracker.

    ByteTrack state belongs to one video. The detector is a process-wide
    singleton, and FastAPI runs `def` endpoints in a threadpool, so two
    overlapping requests — a /events/run job in a background thread while a
    /checkin call arrives, say — would interleave frames from different videos
    into the same tracker and mangle both. Nothing prevented that before;
    'videos are processed sequentially' was true only within a single job.

    Resetting on the way OUT as well as in means the next caller starts clean
    even if this one raised mid-video.
    """
    detector = get_detector()
    with _tracking_lock:
        detector.reset_tracker()
        try:
            yield detector
        finally:
            detector.reset_tracker()


def get_detector(conf: float = 0.30, iou: float = 0.50) -> PersonObjectDetector:
    """Returns the cached detector. Conf/iou can be overridden per-call via .track()."""
    global _detector
    with _lock:
        if _detector is None:
            _detector = PersonObjectDetector(conf=conf, iou=iou)
    return _detector
