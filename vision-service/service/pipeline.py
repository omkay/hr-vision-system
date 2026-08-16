"""Reusable pipeline runner — detection → tracking → identity → events."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np
import pandas as pd

from .config import (
    DEFAULT_DET_CONF, DEFAULT_DET_IOU, DEFAULT_FACE_THR, DEFAULT_FUSE_WIN,
    DEFAULT_MAX_FRAMES, DEFAULT_PROX_PX, DEFAULT_REID_THR, DEFAULT_STRIDE, OUT_DIR,
)
from .events_engine import EventEngine, Zone, load_zones_for_video
from .schemas import ZoneDefinition
from .gallery import EmployeeGallery
from .identity import IdentityFuser, UNKNOWN_LABEL
from .models import get_detector, get_face_embedder, get_reid_embedder
from .storage import resolve_source


def run_pipeline(
    video_path: str,
    gallery: Optional[EmployeeGallery] = None,
    *,
    zones: Optional[List[ZoneDefinition]] = None,
    camera_id: str = "cam0",
    det_conf: float = DEFAULT_DET_CONF,
    det_iou: float = DEFAULT_DET_IOU,
    face_thr: float = DEFAULT_FACE_THR,
    reid_thr: float = DEFAULT_REID_THR,
    fuse_win: int = DEFAULT_FUSE_WIN,
    stride: int = DEFAULT_STRIDE,
    max_frames: int = DEFAULT_MAX_FRAMES,
    prox_px: int = DEFAULT_PROX_PX,
    write_video: bool = False,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[pd.DataFrame, Optional[Path]]:
    """Process a video and return (events_df, annotated_video_path)."""
    with resolve_source(video_path) as local_path:
        return _run_pipeline_local(
            local_path, gallery,
            zones=zones, camera_id=camera_id,
            det_conf=det_conf, det_iou=det_iou,
            face_thr=face_thr, reid_thr=reid_thr,
            fuse_win=fuse_win, stride=stride,
            max_frames=max_frames, prox_px=prox_px,
            write_video=write_video, progress=progress,
        )


def _run_pipeline_local(
    video_path: str,
    gallery: Optional[EmployeeGallery] = None,
    *,
    zones: Optional[List[ZoneDefinition]] = None,
    camera_id: str = "cam0",
    det_conf: float = DEFAULT_DET_CONF,
    det_iou: float = DEFAULT_DET_IOU,
    face_thr: float = DEFAULT_FACE_THR,
    reid_thr: float = DEFAULT_REID_THR,
    fuse_win: int = DEFAULT_FUSE_WIN,
    stride: int = DEFAULT_STRIDE,
    max_frames: int = DEFAULT_MAX_FRAMES,
    prox_px: int = DEFAULT_PROX_PX,
    write_video: bool = False,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[pd.DataFrame, Optional[Path]]:
    """Internal: run pipeline on a guaranteed-local path."""
    vp = Path(video_path)
    if not vp.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    detector = get_detector()
    face_emb = get_face_embedder()
    reid_emb = get_reid_embedder()

    # Reset ByteTrack state so this video starts with clean track IDs.
    detector.reset_tracker()

    fuser = None
    if gallery is not None and len(gallery.names) > 0:
        fuser = IdentityFuser(gallery, face_emb, reid_emb,
                              face_thr=face_thr, reid_thr=reid_thr, window=fuse_win)

    cap = cv2.VideoCapture(str(vp))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    annotated_path: Optional[Path] = None
    writer = None
    if write_video:
        annotated_path = OUT_DIR / f"{vp.stem}_annotated.mp4"
        writer = cv2.VideoWriter(str(annotated_path),
                                 cv2.VideoWriter_fourcc(*"mp4v"), 15, (W, H))

    if zones:
        # Resolve ZoneDefinition objects → Zone objects.
        # Any coord left as None defaults to the corresponding frame edge.
        active_zones = [
            Zone.rect(
                z.label,
                z.x1 if z.x1 is not None else 0,
                z.y1 if z.y1 is not None else 0,
                z.x2 if z.x2 is not None else W,
                z.y2 if z.y2 is not None else H,
                zone_type=z.zone_type,
            )
            for z in zones
        ]
    else:
        # No zones in the request — check zones_config.json, else full frame.
        active_zones = load_zones_for_video(vp.name, frame_size=(W, H))

    engine = EventEngine(fps=fps / stride, zones=active_zones, proximity_px=prox_px)

    idx = processed = 0
    try:
        while processed < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride != 0:
                idx += 1
                continue
            dets = detector.track(frame, conf=det_conf, iou=det_iou)
            people, phones, laptops, monitors = [], [], [], []
            for d in dets:
                if d["cls_name"] == "person":
                    x1, y1, x2, y2 = d["bbox"]
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(W, x2), min(H, y2)
                    crop = frame[y1:y2, x1:x2]
                    name = UNKNOWN_LABEL
                    if fuser is not None and crop.size > 0:
                        name = fuser.update(d["track_id"], crop)
                    people.append({**d, "employee_id": name})
                elif d["cls_name"] == "cell phone":
                    phones.append(d)
                elif d["cls_name"] == "laptop":
                    laptops.append(d)
                elif d["cls_name"] == "monitor":
                    monitors.append(d)
            engine.update(processed, people, phones, laptops, monitors)
            if writer is not None:
                writer.write(frame)
            idx += 1
            processed += 1
            if progress is not None and processed % 10 == 0:
                progress(processed, max_frames)
    finally:
        cap.release()
        if writer is not None:
            writer.release()

    engine.flush(processed)
    df = engine.to_dataframe()
    if not df.empty:
        df.insert(0, "camera_id", camera_id)
    return df, annotated_path


def _checkin_bgr(img: np.ndarray, gallery: EmployeeGallery,
                 face_thr: float = DEFAULT_FACE_THR,
                 reid_thr: float = DEFAULT_REID_THR) -> dict:
    """Core checkin logic on a BGR numpy array.

    Returns a dict with employee_id, confidence, and method.
    Internal helper — callers should use checkin() or checkin_video().
    """
    face_emb = get_face_embedder()
    reid_emb = get_reid_embedder()

    fuser = IdentityFuser(gallery, face_emb, reid_emb,
                          face_thr=face_thr, reid_thr=reid_thr)
    face_match = fuser.match_face(img)
    if face_match is not None:
        return {"employee_id": face_match[0], "confidence": face_match[1], "method": "face"}

    # Fall back to detector → largest person crop → ReID.
    detector = get_detector()
    dets = detector.track(img)
    persons = [d for d in dets if d["cls_name"] == "person"]
    if not persons:
        return {"employee_id": UNKNOWN_LABEL, "confidence": 0.0, "method": "none"}
    persons.sort(
        key=lambda d: (d["bbox"][2] - d["bbox"][0]) * (d["bbox"][3] - d["bbox"][1]),
        reverse=True,
    )
    x1, y1, x2, y2 = persons[0]["bbox"]
    H, W = img.shape[:2]
    crop = img[max(0, y1):min(H, y2), max(0, x1):min(W, x2)]
    if crop.size == 0:
        return {"employee_id": UNKNOWN_LABEL, "confidence": 0.0, "method": "none"}
    reid_match = fuser.match_reid(crop)
    if reid_match is not None:
        return {"employee_id": reid_match[0], "confidence": reid_match[1], "method": "reid"}
    return {"employee_id": UNKNOWN_LABEL, "confidence": 0.0, "method": "none"}


def checkin(image_path: str, gallery: EmployeeGallery,
            face_thr: float = DEFAULT_FACE_THR,
            reid_thr: float = DEFAULT_REID_THR) -> dict:
    """Identify the most prominent person in a single image.

    *image_path* may be a local path, an HTTP/HTTPS URL, or an S3 URI.
    Returns a dict with employee_id, confidence, and method.
    """
    with resolve_source(image_path) as local_path:
        img = cv2.imread(local_path)
        if img is None:
            raise FileNotFoundError(f"Could not read image: {image_path}")
        return _checkin_bgr(img, gallery, face_thr=face_thr, reid_thr=reid_thr)


def checkin_video(
    source: str,
    gallery: EmployeeGallery,
    *,
    face_thr: float = DEFAULT_FACE_THR,
    reid_thr: float = DEFAULT_REID_THR,
    stride: int = 5,
    motion_thr: float = 0.013,
    blur_thr: float = 183.0,
    early_exit_conf: float = 0.77,
    max_frames: int = 500,
) -> dict:
    """Identify the most prominent person across a video file or camera stream.

    Applies a three-stage filter funnel to skip useless frames before running
    any expensive model inference:

    Stage 1 — Motion gate  (~0 ms): frame-difference below *motion_thr* → skip.
        Eliminates long static stretches (empty corridors, idle scenes).

    Stage 2 — Blur gate   (~1 ms): Laplacian variance below *blur_thr* → skip.
        Eliminates motion-blurred or out-of-focus frames where face models fail.

    Stage 3 — Face+ReID  (~50 ms): run _checkin_bgr only on sharp, moving frames.

    Early exit: once a match with confidence ≥ *early_exit_conf* is found the
    function returns immediately without reading more frames.

    Vote aggregation: when no single frame hits the early-exit threshold the
    function collects all per-frame hits and returns the identity with the most
    votes (ties broken by highest confidence).

    Args:
        source:           Path to a video file, or an RTSP / webcam URL/index
                          accepted by cv2.VideoCapture.
        gallery:          Enrolled employee gallery.
        stride:           Process every Nth frame (reduces CPU without hurting
                          accuracy much — people don't move that fast).
        motion_thr:       Fraction of pixels that must change between frames for
                          a frame to be considered "active".  0.01 = 1%.
        blur_thr:         Minimum Laplacian variance for a frame to be considered
                          sharp.  Typical values: 50 (lenient) – 200 (strict).
        early_exit_conf:  Stop processing as soon as a match exceeds this score.
        max_frames:       Hard cap on processed frames (safety for long streams).

    Returns a dict with:
        employee_id, confidence, method — best match (or UNKNOWN).
        frames_read        — total frames pulled from the source.
        frames_processed   — frames that passed all gates and ran inference.
        skipped_motion     — frames dropped by the motion gate.
        skipped_blur       — frames dropped by the blur gate.
        skipped_no_face    — frames that were sharp/moving but had no face/person.
    """
    # resolve_source handles HTTP/S3 downloads; streams/local paths pass through.
    with resolve_source(source) as local_path:
        return _checkin_video_local(
            local_path, gallery,
            face_thr=face_thr, reid_thr=reid_thr,
            stride=stride, motion_thr=motion_thr, blur_thr=blur_thr,
            early_exit_conf=early_exit_conf, max_frames=max_frames,
        )


def _checkin_video_local(
    source: str,
    gallery: EmployeeGallery,
    *,
    face_thr: float = DEFAULT_FACE_THR,
    reid_thr: float = DEFAULT_REID_THR,
    stride: int = 5,
    motion_thr: float = 0.013,
    blur_thr: float = 183.0,
    early_exit_conf: float = 0.77,
    max_frames: int = 500,
) -> dict:
    """Internal: run checkin_video on a local path or stream URI."""
    cap = cv2.VideoCapture(source if not str(source).isdigit() else int(source))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video source: {source}")

    stats = dict(frames_read=0, frames_processed=0,
                 skipped_motion=0, skipped_blur=0, skipped_no_face=0)
    candidates: List[dict] = []
    prev_gray: Optional[np.ndarray] = None
    frame_idx = 0

    try:
        while stats["frames_read"] < max_frames * stride:
            ok, frame = cap.read()
            if not ok:
                break
            stats["frames_read"] += 1

            # ── Stride: only inspect every Nth frame ──────────────────────────
            if frame_idx % stride != 0:
                frame_idx += 1
                continue
            frame_idx += 1

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # ── Stage 1: Motion gate ───────────────────────────────────────────
            if prev_gray is not None:
                diff = cv2.absdiff(gray, prev_gray)
                changed = float(np.count_nonzero(diff > 25)) / diff.size
                if changed < motion_thr:
                    prev_gray = gray
                    stats["skipped_motion"] += 1
                    continue
            prev_gray = gray

            # ── Stage 2: Blur gate ─────────────────────────────────────────────
            lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            if lap_var < blur_thr:
                stats["skipped_blur"] += 1
                continue

            # ── Stage 3: Face + ReID inference ────────────────────────────────
            stats["frames_processed"] += 1
            result = _checkin_bgr(frame, gallery, face_thr=face_thr, reid_thr=reid_thr)

            if result["employee_id"] == UNKNOWN_LABEL:
                stats["skipped_no_face"] += 1
                continue

            candidates.append(result)

            # Early exit: confident enough, no need to keep scanning
            if result["confidence"] >= early_exit_conf:
                break

    finally:
        cap.release()

    if not candidates:
        return {
            "employee_id": UNKNOWN_LABEL, "confidence": 0.0, "method": "none",
            **stats,
        }

    # Vote: most frequent identity wins; ties broken by highest confidence
    votes = Counter(c["employee_id"] for c in candidates)
    best_id = votes.most_common(1)[0][0]
    best = max((c for c in candidates if c["employee_id"] == best_id),
               key=lambda c: c["confidence"])
    return {**best, "votes": dict(votes), **stats}
