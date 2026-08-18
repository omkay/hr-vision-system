"""Reusable pipeline runner — detection → tracking → identity → events."""
from __future__ import annotations

from collections import Counter
from datetime import date as _date
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np
import pandas as pd

from .config import (
    DEFAULT_DET_CONF, DEFAULT_DET_IOU, DEFAULT_FACE_THR, DEFAULT_FUSE_WIN,
    DEFAULT_MAX_FRAMES, DEFAULT_PROX_PX, DEFAULT_REID_THR, DEFAULT_STRIDE,
    DETECTION_MAX_DIM, OUT_DIR,
)
from . import daily_gallery as daily_gallery_store
from .events_engine import EventEngine, Zone, load_zones_for_video
from .schemas import ZoneDefinition
from .gallery import EmployeeGallery
from .identity import IdentityFuser, UNKNOWN_LABEL
from .models import get_detector, get_face_embedder, get_reid_embedder
from .storage import resolve_source

# Cap on how many body crops we keep per track while scanning a checkin
# video, purely to bound memory/CPU for the end-of-run ReID embedding batch
# — a handful of clean crops from across the clip is plenty to build a
# same-day appearance reference, no need to embed every frame the person
# appeared in.
MAX_DAILY_FINGERPRINT_CROPS = 10


def _detection_scale(width: int, height: int, max_dim: int = DETECTION_MAX_DIM) -> float:
    """Scale factor to bring the longest side down to max_dim (1.0 = no change)."""
    longest = max(width, height)
    return 1.0 if longest <= max_dim else max_dim / longest


def _scaled_size(width: int, height: int, scale: float) -> tuple[int, int]:
    return max(1, round(width * scale)), max(1, round(height * scale))


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
    session_date: Optional[str] = None,
) -> Tuple[pd.DataFrame, Optional[Path]]:
    """Process a video and return (events_df, annotated_video_path).

    session_date: which day's daily body-fingerprint gallery (see
    daily_gallery.py) to prefer during ReID matching, in YYYY-MM-DD form —
    normally the date of the checkin video that seeded that day's
    fingerprints for the employees expected in this footage. Defaults to
    today if omitted. Has no effect if no fingerprints exist yet for that
    date (falls straight through to the static enrollment gallery).
    """
    with resolve_source(video_path) as local_path:
        return _run_pipeline_local(
            local_path, gallery,
            zones=zones, camera_id=camera_id,
            det_conf=det_conf, det_iou=det_iou,
            face_thr=face_thr, reid_thr=reid_thr,
            fuse_win=fuse_win, stride=stride,
            max_frames=max_frames, prox_px=prox_px,
            write_video=write_video, progress=progress,
            session_date=session_date,
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
    session_date: Optional[str] = None,
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

    daily = daily_gallery_store.load_daily_gallery(session_date or _date.today().isoformat())

    fuser = None
    if gallery is not None and len(gallery.names) > 0:
        fuser = IdentityFuser(gallery, face_emb, reid_emb,
                              face_thr=face_thr, reid_thr=reid_thr, window=fuse_win,
                              daily_gallery=daily)

    cap = cv2.VideoCapture(str(vp))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    native_W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    native_H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    # Downscale once up front — every frame is resized to this before anything
    # else touches it, so zones/detections/crops/annotated output all stay in
    # one consistent coordinate space with no separate rescale bookkeeping.
    det_scale = _detection_scale(native_W, native_H)
    W, H = _scaled_size(native_W, native_H, det_scale) if det_scale != 1.0 else (native_W, native_H)

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
            if det_scale != 1.0:
                frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)
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


def checkin_video_multi(
    source: str,
    gallery: EmployeeGallery,
    *,
    face_thr: float = DEFAULT_FACE_THR,
    reid_thr: float = DEFAULT_REID_THR,
    stride: int = DEFAULT_STRIDE,
    max_frames: int = DEFAULT_MAX_FRAMES,
    det_conf: float = DEFAULT_DET_CONF,
    det_iou: float = DEFAULT_DET_IOU,
    session_date: Optional[str] = None,
) -> dict:
    """Identify EVERY distinct person appearing in a video — not just one.

    checkin_video() answers "who is the most prominent person here", with an
    early exit as soon as it's confident about anyone — correct for a kiosk
    "one person walks up" scenario, but wrong for "who all passed by this
    camera", since early exit stops scanning the instant the FIRST person is
    confidently matched, before the rest of the video (and anyone else in it)
    is ever looked at.

    This instead tracks every person via ByteTrack (the same tracker the
    zone/events pipeline uses) and resolves an identity per track with the
    same IdentityFuser — a rolling weighted vote per track_id that commits
    once a track has enough consistent evidence. No early exit: completeness
    is the goal here, not speed, so the whole video (or max_frames) is scanned.

    Also treats this scan as "the day's checkin": for every employee who
    reaches a committed identity, body crops collected from their own
    track(s) are embedded via ReID and saved as that employee's daily body
    fingerprint (see daily_gallery.py), keyed by session_date (defaults to
    today). Zone videos processed later for the same date will then prefer
    matching against this fresh, same-day appearance over the static
    enrollment-time bank — see IdentityFuser.match_reid.

    Returns a dict with:
        matches           — list of {employee_id, confidence}, one per
                             distinct identified person (UNKNOWN tracks and
                             tracks that never accumulated enough votes to
                             commit are excluded — see IdentityFuser.update).
        num_tracks        — how many committed identity tracks were found.
        session_date      — the date these daily fingerprints were saved under.
        frames_read / frames_processed — same bookkeeping as checkin_video().
    """
    with resolve_source(source) as local_path:
        return _checkin_video_multi_local(
            local_path, gallery,
            face_thr=face_thr, reid_thr=reid_thr,
            stride=stride, max_frames=max_frames,
            det_conf=det_conf, det_iou=det_iou,
            session_date=session_date,
        )


def _checkin_video_multi_local(
    source: str,
    gallery: EmployeeGallery,
    *,
    face_thr: float = DEFAULT_FACE_THR,
    reid_thr: float = DEFAULT_REID_THR,
    stride: int = DEFAULT_STRIDE,
    max_frames: int = DEFAULT_MAX_FRAMES,
    det_conf: float = DEFAULT_DET_CONF,
    det_iou: float = DEFAULT_DET_IOU,
    session_date: Optional[str] = None,
) -> dict:
    """Internal: run checkin_video_multi on a local path or stream URI."""
    detector = get_detector()
    face_emb = get_face_embedder()
    reid_emb = get_reid_embedder()

    # Reset ByteTrack state so this video starts with clean track IDs,
    # same as _run_pipeline_local does for the zone/events path.
    detector.reset_tracker()

    fuser = IdentityFuser(gallery, face_emb, reid_emb,
                          face_thr=face_thr, reid_thr=reid_thr)

    cap = cv2.VideoCapture(source if not str(source).isdigit() else int(source))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video source: {source}")

    stats = dict(frames_read=0, frames_processed=0)
    # track_id -> (employee_id, best face-match confidence seen for that track).
    # Confidence is tracked from match_face() directly (not IdentityFuser's
    # internal vote weight) so the reported number means the same thing as
    # checkin()/checkin_video()'s "confidence": a cosine similarity score.
    track_best: dict = {}
    # track_id -> list of body crops, capped, used at the end to build each
    # committed employee's daily ReID fingerprint (see daily_gallery.py).
    track_crops: dict = {}
    idx = processed = 0

    try:
        while processed < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            stats["frames_read"] += 1

            if idx % stride != 0:
                idx += 1
                continue

            native_h, native_w = frame.shape[:2]
            scale = _detection_scale(native_w, native_h)
            if scale != 1.0:
                frame = cv2.resize(frame, _scaled_size(native_w, native_h, scale),
                                    interpolation=cv2.INTER_AREA)
            H, W = frame.shape[:2]
            dets = detector.track(frame, conf=det_conf, iou=det_iou)
            for d in dets:
                if d["cls_name"] != "person":
                    continue
                x1, y1, x2, y2 = d["bbox"]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(W, x2), min(H, y2)
                crop = frame[y1:y2, x1:x2]
                if crop.size == 0:
                    continue

                track_id = d["track_id"]
                face_match = fuser.match_face(crop)
                confidence = face_match[1] if face_match is not None else 0.0

                # update() runs the actual face/ReID match again internally
                # to drive the per-track committed vote — slightly redundant
                # with match_face() above, but keeps this function a thin
                # wrapper around the existing, already-tested fusion logic
                # rather than re-implementing the commit rule here too.
                name = fuser.update(track_id, crop)
                if name == UNKNOWN_LABEL:
                    continue

                prev = track_best.get(track_id)
                if prev is None or confidence > prev[1]:
                    track_best[track_id] = (name, confidence)

                crops_so_far = track_crops.setdefault(track_id, [])
                if len(crops_so_far) < MAX_DAILY_FINGERPRINT_CROPS:
                    crops_so_far.append(crop.copy())

            idx += 1
            processed += 1
    finally:
        cap.release()

    # Only report tracks IdentityFuser actually committed to an identity —
    # same bar checkin_video() implicitly uses via vote aggregation, so a
    # fleeting misdetection can't show up as "this person was here".
    committed_tracks = {
        track_id: name for track_id, name in fuser.committed.items()
        if name != UNKNOWN_LABEL
    }

    # A person can span multiple tracks if they leave and re-enter frame —
    # collapse to one entry per employee, keeping their best confidence.
    by_employee: dict = {}
    for track_id, name in committed_tracks.items():
        conf = track_best.get(track_id, (name, 0.0))[1]
        if name not in by_employee or conf > by_employee[name]:
            by_employee[name] = conf

    matches = [
        {"employee_id": name, "confidence": round(conf, 4)}
        for name, conf in sorted(by_employee.items(), key=lambda kv: -kv[1])
    ]

    # Generate + persist today's body fingerprint for every committed
    # employee — pool crops across all of that employee's tracks (handles
    # the "left frame and re-entered" case, same as the by_employee merge
    # above) so a track that only got a couple of usable crops still
    # benefits from a same-person track recorded elsewhere in the clip.
    session_date = session_date or _date.today().isoformat()
    crops_by_employee: dict = {}
    for track_id, name in committed_tracks.items():
        crops_by_employee.setdefault(name, []).extend(track_crops.get(track_id, []))
    for name, crops in crops_by_employee.items():
        if not crops:
            continue
        vecs = reid_emb.embed_batch(crops)
        daily_gallery_store.save_fingerprint(session_date, name, vecs)

    stats["frames_processed"] = processed
    return {
        "matches": matches,
        "num_tracks": len(committed_tracks),
        "session_date": session_date,
        **stats,
    }
