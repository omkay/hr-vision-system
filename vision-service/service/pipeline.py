"""Reusable pipeline runner — detection → tracking → identity → events."""
from __future__ import annotations

import logging
import subprocess
from collections import Counter
from datetime import date as _date
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np
import pandas as pd

from .config import (
    DEFAULT_ANNOTATE_OUTPUT_FPS, DEFAULT_ANNOTATE_STRIDE, DEFAULT_DET_CONF,
    DEFAULT_DET_IOU, DEFAULT_FACE_THR, DEFAULT_FUSE_WIN, DEFAULT_MAX_FRAMES,
    DEFAULT_PROX_PX, DEFAULT_REID_MARGIN, DEFAULT_REID_THR, DEFAULT_STRIDE,
    DETECTION_MAX_DIM,
    IDENTITY_CROPS_AT_NATIVE_RES, OUT_DIR,
)
from .quality import normalize_illumination
from . import daily_gallery as daily_gallery_store
from .events_engine import EventEngine, Zone, load_zones_for_video, filter_objects_near_people
from .schemas import ZoneDefinition
from .gallery import EmployeeGallery
from .identity import IdentityFuser, UNKNOWN_LABEL
from .models import (
    get_detector, get_face_embedder, get_reid_embedder, serialized_tracking,
)
from .storage import resolve_source

log = logging.getLogger(__name__)

# ── Annotated-video drawing ──────────────────────────────────────────────────
# Colors are BGR (OpenCV convention), chosen to be distinguishable from each
# other and from typical office-footage backgrounds.
_COLOR_KNOWN = (60, 200, 60)      # green — identified employee
_COLOR_UNKNOWN = (140, 140, 140)  # gray — UNKNOWN / uncommitted track
_COLOR_PHONE = (0, 200, 255)
_COLOR_LAPTOP = (255, 200, 0)
_COLOR_MONITOR = (255, 120, 0)
_COLOR_ZONE = (255, 150, 0)


def _draw_box(frame, bbox, color, label=None, thickness=2):
    x1, y1, x2, y2 = [int(v) for v in bbox]
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
    if label:
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        ty = max(th + 4, y1)
        cv2.rectangle(frame, (x1, ty - th - 6), (x1 + tw + 6, ty), color, -1)
        cv2.putText(frame, label, (x1 + 3, ty - 3), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 0, 0), 1, cv2.LINE_AA)


def _draw_zones(frame, zones):
    """Draw each zone's polygon boundary + label — lets a reviewer see the
    exact region presence/working/interaction events are being tested
    against, not just the raw detections."""
    for z in zones:
        try:
            coords = list(z.polygon.exterior.coords)
        except Exception:
            continue
        pts = np.array(coords, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(frame, [pts], isClosed=True, color=_COLOR_ZONE,
                      thickness=1, lineType=cv2.LINE_AA)
        x0, y0 = pts[0][0]
        cv2.putText(frame, z.name, (int(x0) + 4, int(y0) + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, _COLOR_ZONE, 1, cv2.LINE_AA)


def _annotate_frame(frame, people, phones, laptops, monitors, zones, ts_s):
    """Draw zones, person boxes (colored/labeled by resolved identity), and
    object boxes directly onto *frame* in place, plus a timestamp so a
    reviewer can cross-reference against event start_s/end_s. This is the
    only place bounding boxes actually get drawn — the pipeline previously
    named its output "annotated_videos" without ever drawing anything."""
    _draw_zones(frame, zones)
    for p in people:
        known = p["employee_id"] != UNKNOWN_LABEL
        color = _COLOR_KNOWN if known else _COLOR_UNKNOWN
        _draw_box(frame, p["bbox"], color, f'{p["employee_id"]} #{p["track_id"]}')
    for dets, color, name in (
        (phones, _COLOR_PHONE, "phone"),
        (laptops, _COLOR_LAPTOP, "laptop"),
        (monitors, _COLOR_MONITOR, "monitor"),
    ):
        for d in dets:
            _draw_box(frame, d["bbox"], color, name)
    cv2.putText(frame, f"t={ts_s:.1f}s", (8, frame.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return frame


def _bbox_center(bbox):
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


# Spatiotemporal track re-linking — see IdentityFuser.adopt() for the
# rationale. A brand-new track_id appearing within this many seconds, and
# this close (pixels, in the already-downscaled coordinate space), to where
# a committed identity's track was last seen is presumed to be the same
# physical person continuing on (ByteTrack lost them, most often because
# their visible appearance changed — e.g. removing a jacket — right as
# same-day ReID would otherwise need to re-recognize that new appearance
# and may not be able to).
RELINK_MAX_GAP_S = 2.0
RELINK_MAX_DIST_PX = 200


def _find_relink_candidate(new_bbox, processed_idx, fps, track_last_seen, committed):
    """Find a recently-vacated committed track near *new_bbox*, if any.

    Returns (donor_track_id, name) or None. Geometry only — the caller MUST
    additionally pass the result through IdentityFuser.can_relink(), which
    verifies that the new crop actually resembles the donor track's last
    known appearance and that the donor's identity isn't already visible on
    another live track. Proximity on its own is at its least reliable
    exactly where it fires most often (doorways, corners, the check-in
    queue), because a stream of different people pass through the same few
    hundred pixels within seconds of each other — which is how a corner
    detection ended up wearing someone else's employee ID.
    """
    max_gap_frames = RELINK_MAX_GAP_S * fps
    nx, ny = _bbox_center(new_bbox)
    best, best_dist = None, RELINK_MAX_DIST_PX
    for tid, name in committed.items():
        seen = track_last_seen.get(tid)
        if seen is None:
            continue
        last_frame, last_bbox = seen
        gap = processed_idx - last_frame
        if gap <= 0 or gap > max_gap_frames:
            continue
        lx, ly = _bbox_center(last_bbox)
        dist = ((nx - lx) ** 2 + (ny - ly) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best = (tid, name)
    return best


def _resolve_identities(fuser, person_dets, processed_idx, fps,
                        track_last_seen, frame_w, frame_h):
    """Resolve every person in one frame to a label, in three phases.

    Phase 1 (observe) gates each crop on quality and scores it against the
    gallery without deciding anything. Phase 2 attempts verified track
    re-linking for brand-new track IDs. Phase 3 solves the whole frame as a
    single assignment problem, so "one employee appears at most once per
    frame" holds by construction.

    This ordering is what replaced the old post-hoc conflict handler
    (_resolve_same_frame_conflicts → IdentityFuser.revoke), which detected
    the impossible state after the fact and dealt with it by blacklisting
    the contested name for the remainder of the video — permanently
    un-identifying a correctly-matched employee because some other track
    had a false positive.

    Returns ({track_id: employee_id}, [Observation, ...]) — the observations
    are handed back so callers can reuse the scores/quality verdict already
    computed here (e.g. checkin's daily-fingerprint crop selection) instead
    of re-running the models on the same crop.
    """
    observations = []
    bboxes = {}
    for d, crop in person_dets:
        obs = fuser.observe(d["track_id"], crop, d["bbox"], frame_w, frame_h)
        observations.append(obs)
        bboxes[d["track_id"]] = d["bbox"]

    live_ids = [o.track_id for o in observations]
    for obs in observations:
        tid = obs.track_id
        if tid in track_last_seen or tid in fuser.committed or not obs.usable:
            continue
        candidate = _find_relink_candidate(
            bboxes[tid], processed_idx, fps, track_last_seen, fuser.committed,
        )
        if candidate is not None and fuser.can_relink(obs, candidate[0], live_ids):
            fuser.adopt(tid, candidate[1])

    return fuser.resolve_frame(observations, frame_idx=processed_idx), observations


def _write_identity_debug(rows: list, video_stem: str,
                          camera_id: str = "") -> Optional[Path]:
    """Dump per-frame identity decisions to CSV for threshold tuning.

    The thresholds in config.py were arrived at by trial and error (0.75 →
    0.60 → 0.65) because no similarity score was ever recorded — every
    retune was a guess evaluated by re-watching annotated video. With this,
    a run produces the actual score distribution for the footage in
    question: what the true match scored, what the false positive scored,
    how large the margins were, and which crops the quality gate dropped.
    """
    if not rows:
        return None
    # Remote sources are downloaded to a random temp file, so the video stem
    # is something like "tmp3i07t5qo" — useless for telling which camera a
    # CSV belongs to. Lead with the camera_id when there is one.
    label = f"cam{camera_id}" if camera_id.isdigit() else camera_id
    prefix = f"{label}_" if label else ""
    path = OUT_DIR / f"{prefix}{video_stem}_identity_debug.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    log.info("identity debug log written: %s (%d rows)", path, len(rows))
    return path


def _compress_video(path: Path) -> Path:
    """Re-encode *path* in place via ffmpeg (H.264, CRF 26) — cv2.VideoWriter's
    mp4v codec is far less efficient than a real encoder for the same visual
    quality, and this is on top of the frame-skip (DEFAULT_ANNOTATE_STRIDE)
    and resolution cap (DETECTION_MAX_DIM) already applied upstream. Falls
    back to leaving the raw mp4v file in place if ffmpeg isn't installed or
    the re-encode fails — a bigger-but-playable file beats no file.
    """
    compressed = path.with_name(path.stem + "_x264.mp4")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-vcodec", "libx264",
             "-crf", "26", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             str(compressed)],
            check=True, capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        log.warning("ffmpeg re-encode failed (%s) — keeping raw mp4v output for %s", e, path)
        return path
    path.unlink(missing_ok=True)
    compressed.rename(path)
    return path

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


def _identity_crop(native_frame, scaled_frame, bbox, det_scale: float):
    """Crop a person for identity matching, at the best resolution available.

    Detection runs on a frame downscaled to DETECTION_MAX_DIM, which is fine
    for YOLO (it resizes internally regardless) but wasteful for identity: on
    4K footage that left people 111-138 px tall, and OSNet then UPSCALES to
    its 256x128 input, so a third of the embedding's input is interpolation.
    Face matching suffers the same way, and worse — InsightFace needs real
    facial detail, not a smoothed 30 px face.

    The bbox is in the scaled coordinate space (everything downstream —
    zones, annotated video — uses it), so it is mapped back up by
    1/det_scale to slice the original frame. No extra model inference, just a
    different slice.
    """
    if not IDENTITY_CROPS_AT_NATIVE_RES or det_scale == 1.0 or native_frame is None:
        h, w = scaled_frame.shape[:2]
        x1, y1, x2, y2 = bbox
        return scaled_frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]

    h, w = native_frame.shape[:2]
    x1, y1, x2, y2 = (int(round(v / det_scale)) for v in bbox)
    return native_frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]


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
    reid_margin: float = DEFAULT_REID_MARGIN,
    fuse_win: int = DEFAULT_FUSE_WIN,
    stride: int = DEFAULT_STRIDE,
    max_frames: int = DEFAULT_MAX_FRAMES,
    prox_px: int = DEFAULT_PROX_PX,
    write_video: bool = False,
    annotate_stride: int = DEFAULT_ANNOTATE_STRIDE,
    progress: Optional[Callable[[int, int], None]] = None,
    on_event: Optional[Callable[[dict], None]] = None,
    session_date: Optional[str] = None,
    debug_identity: bool = False,
    stats_out: Optional[dict] = None,
) -> Tuple[pd.DataFrame, Optional[Path]]:
    """Process a video and return (events_df, annotated_video_path).

    session_date: which day's daily body-fingerprint gallery (see
    daily_gallery.py) to prefer during ReID matching, in YYYY-MM-DD form —
    normally the date of the checkin video that seeded that day's
    fingerprints for the employees expected in this footage. Defaults to
    today if omitted. Has no effect if no fingerprints exist yet for that
    date (falls straight through to the static enrollment gallery).

    annotate_stride: only relevant when write_video=True — write 1 out of
    every N *processed* frames to the output, to keep the annotated video
    reviewable-by-eye without ballooning file size. See config.py's
    DEFAULT_ANNOTATE_STRIDE for the reasoning.

    on_event: optional callback invoked the instant each event is finalized
    (mid-video, well before this function returns) — see EventEngine's
    on_event param. Lets callers surface activity events as they're
    detected rather than only once the whole video has been processed.
    """
    with resolve_source(video_path) as local_path:
        return _run_pipeline_local(
            local_path, gallery,
            zones=zones, camera_id=camera_id,
            det_conf=det_conf, det_iou=det_iou,
            face_thr=face_thr, reid_thr=reid_thr, reid_margin=reid_margin,
            fuse_win=fuse_win, stride=stride,
            max_frames=max_frames, prox_px=prox_px,
            write_video=write_video, annotate_stride=annotate_stride,
            progress=progress, on_event=on_event, session_date=session_date,
            debug_identity=debug_identity, stats_out=stats_out,
        )


@serialized_tracking
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
    reid_margin: float = DEFAULT_REID_MARGIN,
    fuse_win: int = DEFAULT_FUSE_WIN,
    stride: int = DEFAULT_STRIDE,
    max_frames: int = DEFAULT_MAX_FRAMES,
    prox_px: int = DEFAULT_PROX_PX,
    write_video: bool = False,
    annotate_stride: int = DEFAULT_ANNOTATE_STRIDE,
    progress: Optional[Callable[[int, int], None]] = None,
    on_event: Optional[Callable[[dict], None]] = None,
    session_date: Optional[str] = None,
    debug_identity: bool = False,
    stats_out: Optional[dict] = None,
) -> Tuple[pd.DataFrame, Optional[Path]]:
    """Internal: run pipeline on a guaranteed-local path."""
    vp = Path(video_path)
    if not vp.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    # Tracker reset + exclusive access are handled by @serialized_tracking
    # on this function — see models.tracking_session() for why both matter.
    detector = get_detector()
    face_emb = get_face_embedder()
    reid_emb = get_reid_embedder()

    daily = daily_gallery_store.load_daily_gallery(session_date or _date.today().isoformat())

    fuser = None
    if gallery is not None and len(gallery.names) > 0:
        fuser = IdentityFuser(gallery, face_emb, reid_emb,
                              face_thr=face_thr, reid_thr=reid_thr,
                              reid_margin=reid_margin, window=fuse_win,
                              daily_gallery=daily, debug=debug_identity)

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
        annotate_stride = max(1, annotate_stride)
        # Fixed output fps (not compensated for annotate_stride) — see
        # DEFAULT_ANNOTATE_OUTPUT_FPS in config.py for why: this makes
        # playback duration actually shrink with annotate_stride (a genuine
        # time-lapse) instead of silently matching the source clip's real
        # elapsed time no matter how aggressively frames are subsampled.
        out_fps = DEFAULT_ANNOTATE_OUTPUT_FPS
        writer = cv2.VideoWriter(str(annotated_path),
                                 cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (W, H))

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

    def _emit(row: dict, _camera_id=camera_id):
        # The final DataFrame gets `camera_id` inserted as a column only
        # after the whole video is processed (see df.insert(...) below) —
        # tag it here too so a row streamed out mid-video already has the
        # same shape as one read from the final result.
        if on_event is not None:
            on_event({"camera_id": _camera_id, **row})

    engine = EventEngine(fps=fps / stride, zones=active_zones, proximity_px=prox_px,
                         on_event=_emit if on_event is not None else None)

    idx = processed = 0
    # track_id -> (processed_idx, bbox) for every person seen so far, used
    # to feed _find_relink_candidate() when a brand-new track_id shows up.
    track_last_seen: dict = {}
    # Per-video counters, surfaced to the caller (see stats_out). Without
    # these, "this camera returned 0 events" is indistinguishable between:
    # the video never opened, no frames were read, nobody was detected, people
    # were detected but never tracked, tracked but never identified, or
    # identified but every event fell under its min-duration threshold. Each
    # has a different fix, and guessing between them wastes a full re-run.
    n_person_dets = 0
    n_quality_rejects = 0
    last_frame_with_person = -1
    try:
        while processed < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride != 0:
                idx += 1
                continue
            # Keep the original around: detection uses the downscaled frame,
            # identity uses full resolution (see _identity_crop).
            frame_native = frame
            if det_scale != 1.0:
                frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)
            dets = detector.track(frame, conf=det_conf, iou=det_iou)
            person_dets, phones, laptops, monitors = [], [], [], []
            for d in dets:
                if d["cls_name"] == "person":
                    crop = _identity_crop(frame_native, frame, d["bbox"], det_scale)
                    if crop.size > 0:
                        person_dets.append((d, crop))
                elif d["cls_name"] == "cell phone":
                    phones.append(d)
                elif d["cls_name"] == "laptop":
                    laptops.append(d)
                elif d["cls_name"] == "monitor":
                    monitors.append(d)

            # Identity is resolved for the whole frame at once (see
            # _resolve_identities) rather than per track as detections are
            # iterated — one employee can't be two people in one frame, and
            # that constraint can only be honoured with every track's scores
            # on the table simultaneously.
            labels: dict = {}
            n_person_dets += len(person_dets)
            if person_dets:
                last_frame_with_person = processed
            if fuser is not None:
                labels, observations = _resolve_identities(
                    fuser, person_dets, processed, engine.fps,
                    track_last_seen, W, H,
                )
                n_quality_rejects += sum(1 for o in observations if not o.usable)
            people = [
                {**d, "employee_id": labels.get(d["track_id"], UNKNOWN_LABEL)}
                for d, _ in person_dets
            ]
            for d, _ in person_dets:
                track_last_seen[d["track_id"]] = (processed, d["bbox"])

            engine.update(processed, people, phones, laptops, monitors)
            if writer is not None and processed % annotate_stride == 0:
                # Draw only phone/laptop/monitor detections that are actually
                # near a person — the same proximity rule engine.update() just
                # used to decide whether to log a phone_use/working event.
                # Without this, a misclassified background object (dish rack
                # as "cell phone", cleaning robot as "monitor") gets a box
                # drawn in the video even on frames where nobody is anywhere
                # near it and no event was ever logged for it.
                draw_phones = filter_objects_near_people(people, phones, engine.phone_iou, engine.phone_overlap)
                draw_laptops = filter_objects_near_people(people, laptops, engine.work_iou, engine.work_overlap)
                draw_monitors = filter_objects_near_people(people, monitors, engine.work_iou, engine.work_overlap)
                _annotate_frame(frame, people, draw_phones, draw_laptops, draw_monitors,
                               active_zones, processed / engine.fps)
                writer.write(frame)
            idx += 1
            processed += 1
            if progress is not None and processed % 10 == 0:
                progress(processed, max_frames)
    finally:
        cap.release()
        if writer is not None:
            writer.release()

    if annotated_path is not None:
        annotated_path = _compress_video(annotated_path)

    if fuser is not None and debug_identity:
        _write_identity_debug(fuser.debug_rows, vp.stem, camera_id)

    engine.flush(processed)
    df = engine.to_dataframe()
    if not df.empty:
        df.insert(0, "camera_id", camera_id)

    if stats_out is not None:
        identified = {n for n in (fuser.committed.values() if fuser else ()) 
                      if n != UNKNOWN_LABEL}
        stats_out.update({
            "camera_id": camera_id,
            "frames_read": idx,
            "frames_processed": processed,
            "video_fps": round(fps, 2),
            "person_detections": n_person_dets,
            "quality_rejected_crops": n_quality_rejects,
            "distinct_tracks": len(track_last_seen),
            "identified_employees": sorted(identified),
            "last_frame_with_a_person": last_frame_with_person,
            "events_after_min_duration": int(len(df)),
            "identity_enabled": fuser is not None,
        })
        log.info("pipeline stats for camera %s: %s", camera_id, stats_out)

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
    # detect(), not track(): this runs on a single unrelated still, so there
    # is no temporal continuity to exploit — and track() would mutate the
    # shared ByteTrack state, corrupting whatever video is processed next.
    detector = get_detector()
    dets = detector.detect(img)
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
    debug_identity: bool = False,
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
            debug_identity=debug_identity,
        )


@serialized_tracking
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
    debug_identity: bool = False,
) -> dict:
    """Internal: run checkin_video_multi on a local path or stream URI."""
    # Tracker reset + exclusive access: see @serialized_tracking above.
    detector = get_detector()
    face_emb = get_face_embedder()
    reid_emb = get_reid_embedder()

    fuser = IdentityFuser(gallery, face_emb, reid_emb,
                          face_thr=face_thr, reid_thr=reid_thr,
                          debug=debug_identity)

    cap = cv2.VideoCapture(source if not str(source).isdigit() else int(source))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video source: {source}")
    effective_fps = (cap.get(cv2.CAP_PROP_FPS) or 25.0) / stride

    stats = dict(frames_read=0, frames_processed=0)
    # track_id -> (employee_id, best face-match confidence seen for that track).
    # Confidence is tracked from match_face() directly (not IdentityFuser's
    # internal vote weight) so the reported number means the same thing as
    # checkin()/checkin_video()'s "confidence": a cosine similarity score.
    track_best: dict = {}
    # track_id -> list of body crops, capped, used at the end to build each
    # committed employee's daily ReID fingerprint (see daily_gallery.py).
    track_crops: dict = {}
    # Tracks that were identified by an actual face match at least once.
    # Only these seed daily fingerprints: a body-only identification is a
    # provisional guess, and writing a guess into the daily bank would
    # propagate it to every zone video processed for that date (match_reid
    # prefers the daily bank), turning one soft mistake into a whole day of
    # confidently wrong attendance data.
    face_confirmed: set = set()
    # track_id -> (processed_idx, bbox) — feeds _find_relink_candidate(),
    # same spatiotemporal re-linking _run_pipeline_local uses (see there).
    track_last_seen: dict = {}
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
            frame_native = frame
            if scale != 1.0:
                frame = cv2.resize(frame, _scaled_size(native_w, native_h, scale),
                                    interpolation=cv2.INTER_AREA)
            H, W = frame.shape[:2]
            dets = detector.track(frame, conf=det_conf, iou=det_iou)
            person_dets = []
            for d in dets:
                if d["cls_name"] != "person":
                    continue
                crop = _identity_crop(frame_native, frame, d["bbox"], scale)
                if crop.size == 0:
                    continue
                person_dets.append((d, crop))

            # Same frame-level resolution as the zone pipeline — see
            # _resolve_identities. Doing this per detection instead would
            # let one employee be matched to two people standing in the
            # check-in queue at the same time, and this scan is exactly
            # where that mistake is most expensive: its output seeds the
            # whole day's ReID fingerprints.
            labels, observations = _resolve_identities(
                fuser, person_dets, processed, effective_fps,
                track_last_seen, W, H,
            )
            obs_by_track = {o.track_id: o for o in observations}
            for d, crop in person_dets:
                track_id = d["track_id"]
                track_last_seen[track_id] = (processed, d["bbox"])
                name = labels.get(track_id, UNKNOWN_LABEL)
                if name == UNKNOWN_LABEL:
                    continue

                # Confidence reported to callers stays a face cosine
                # similarity, so it means the same thing as
                # checkin()/checkin_video()'s confidence — but it now comes
                # from the observation already computed above instead of a
                # second, redundant match_face() call per detection.
                obs = obs_by_track.get(track_id)
                face_cand = obs.candidate_for(name) if obs is not None else None
                confidence = (face_cand.score
                              if face_cand is not None and face_cand.method == "face"
                              else 0.0)
                prev = track_best.get(track_id)
                if prev is None or confidence > prev[1]:
                    track_best[track_id] = (name, confidence)
                if confidence > 0.0:
                    face_confirmed.add(track_id)

                # Only quality-passing crops are eligible to become part of
                # the day's fingerprint — a truncated or motion-blurred crop
                # baked into the daily bank poisons every zone video
                # processed for that date, since match_reid prefers the
                # daily bank over the enrollment gallery.
                if obs is None or not obs.usable:
                    continue
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
        if track_id not in face_confirmed:
            log.info("skipping daily fingerprint for %s (track %s): no face confirmation",
                     name, track_id)
            continue
        crops_by_employee.setdefault(name, []).extend(track_crops.get(track_id, []))
    for name, crops in crops_by_employee.items():
        if not crops:
            continue
        # Normalised the same way query crops are (see IdentityFuser.observe)
        # — a fingerprint built from raw crops and compared against
        # illumination-normalised queries would sit in a slightly different
        # region of embedding space and cost real similarity.
        vecs = reid_emb.embed_batch([normalize_illumination(c) for c in crops])
        daily_gallery_store.save_fingerprint(session_date, name, vecs)

    if debug_identity:
        # The check-in scan is the root of the whole day's identity chain: it
        # is the only place a face is reliably visible, and its output seeds
        # every other camera's body matching. When zone cameras come back
        # UNKNOWN, this log is the first place to look — it shows whether the
        # face was ever matched here at all, and which crops became the
        # fingerprint.
        _write_identity_debug(fuser.debug_rows, Path(str(source)).stem, "checkin")

    stats["frames_processed"] = processed
    return {
        "matches": matches,
        "num_tracks": len(committed_tracks),
        "session_date": session_date,
        "fingerprinted": sorted(crops_by_employee.keys()),
        **stats,
    }
