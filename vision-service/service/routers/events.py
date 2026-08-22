"""POST /events/run + GET /events/{job_id} — async pipeline job + results."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import (
    DEFAULT_ANNOTATE_STRIDE, DEFAULT_DET_CONF, DEFAULT_DET_IOU, DEFAULT_FACE_THR,
    DEFAULT_FUSE_WIN, DEFAULT_MAX_FRAMES, DEFAULT_PROX_PX, DEFAULT_REID_MARGIN,
    DEFAULT_REID_THR,
    DEFAULT_STRIDE, GALLERY_PATH,
)
from ..gallery import get_gallery
from ..jobs import store
from ..pipeline import run_pipeline
from ..schemas import JobStatusResponse, JobSubmitResponse, ZoneDefinition
from ..storage import is_remote

router = APIRouter(prefix="/events", tags=["events"])


class EventsRequest(BaseModel):
    """Configuration for a multi-camera activity-tracking pipeline run."""

    video_paths: List[str] = Field(
        ...,
        description=(
            "Ordered list of video sources — one per camera. "
            "Accepts local paths, `https://` URLs, or `s3://` URIs."
        ),
        examples=[[
            "/data/corridor_a.mp4",
            "/data/office.mp4",
            "/data/lobby.mp4",
        ]],
    )
    camera_ids: Optional[List[str]] = Field(
        None,
        description=(
            "Human-readable label for each camera, attached to every event. "
            "Must match the length of `video_paths`. "
            "Defaults to `cam0`, `cam1`, … if omitted."
        ),
        examples=[["corridor_a", "office_desks", "main_lobby"]],
    )
    zones: Optional[List[Optional[List[ZoneDefinition]]]] = Field(
        None,
        description=(
            "Per-video zone definitions — one list per video, matching the order of `video_paths`. "
            "Each element is a list of `ZoneDefinition` objects for that camera, "
            "or `null` to use the full frame as a single `work_area`. "
            "Omit the field entirely to apply the full-frame default to all videos."
        ),
    )
    det_conf: float = Field(
        DEFAULT_DET_CONF,
        description="YOLO detection confidence threshold. Lower = more detections.",
    )
    det_iou: float = Field(
        DEFAULT_DET_IOU,
        description="YOLO NMS IoU threshold for suppressing overlapping boxes.",
    )
    face_thr: float = Field(
        DEFAULT_FACE_THR,
        description="Min cosine similarity for a face identity match (0–1).",
    )
    reid_thr: float = Field(
        DEFAULT_REID_THR,
        description="Min cosine similarity for a ReID (body) identity match (0–1).",
    )
    reid_margin: float = Field(
        DEFAULT_REID_MARGIN,
        description=(
            "Min gap between the best and second-best employee's ReID score. This is "
            "the more meaningful of the two knobs for cross-camera matching: absolute "
            "cosine similarity is systematically depressed between different "
            "viewpoints even for the correct person, while the *ranking* stays "
            "reliable. Measured on this system's reception camera, correct matches "
            "sat at 0.58–0.67 (below a 0.65 threshold) but with margins of 0.17–0.31 "
            "and a unanimous winner across every frame of each track."
        ),
    )
    reid_thr_by_camera: Optional[Dict[str, float]] = Field(
        None,
        description=(
            "Per-camera `reid_thr` overrides, keyed by camera_id — cameras with very "
            "different geometry cannot share one threshold. A face-height entrance "
            "camera and a ceiling fisheye produce different score distributions for "
            "the same person, so a single global value is necessarily wrong for one "
            "of them. Cameras not listed use `reid_thr`. Derive the values from a "
            "`debug_identity=true` run rather than by feel."
        ),
        examples=[{"3": 0.55}],
    )
    reid_margin_by_camera: Optional[Dict[str, float]] = Field(
        None,
        description="Per-camera `reid_margin` overrides, keyed by camera_id.",
        examples=[{"3": 0.12}],
    )
    det_conf_by_camera: Optional[Dict[str, float]] = Field(
        None,
        description=(
            "Per-camera `det_conf` overrides, keyed by camera_id. Needed for cameras "
            "whose viewing angle is far from YOLO's COCO training distribution: a "
            "ceiling camera looking almost straight down shows a standing person as "
            "head-and-shoulders with no upright silhouette, and a seated person as a "
            "head above a desk. Those score far below the confidence a ground-level "
            "view would produce, so a camera can return literally zero person "
            "detections at the default 0.30 while people are plainly visible. Check "
            "`diagnostics[].person_detections` before and after changing this."
        ),
        examples=[{"9": 0.15}],
    )
    fuse_win: int = Field(
        DEFAULT_FUSE_WIN,
        description="Number of frames over which identity votes are accumulated per track.",
    )
    stride: int = Field(
        DEFAULT_STRIDE,
        description="Process every Nth frame. Higher = faster, lower temporal resolution.",
    )
    max_frames: int = Field(
        DEFAULT_MAX_FRAMES,
        description="Maximum frames to process per video. Use a small value for smoke tests.",
    )
    prox_px: int = Field(
        DEFAULT_PROX_PX,
        description="Pixel distance threshold for triggering `interaction` events.",
    )
    write_video: bool = Field(
        False,
        description=(
            "If true, save an annotated MP4 per camera — bounding boxes color-coded by "
            "resolved identity, zone outlines, and object boxes for phone/laptop/monitor — "
            "to `outputs/`, servable at `/outputs/<name>` (see `annotated_videos` in the result)."
        ),
    )
    annotate_stride: int = Field(
        DEFAULT_ANNOTATE_STRIDE,
        description=(
            "Only relevant when write_video=true. Writes 1 out of every N *processed* frames "
            "to the annotated video — a human reviewer doesn't need every frame to sanity-check "
            "tracking, just enough density to follow people between zones. Output fps is adjusted "
            "so playback speed still matches real elapsed time despite the skipped frames."
        ),
    )
    debug_identity: bool = Field(
        False,
        description=(
            "If true, write `outputs/<video>_identity_debug.csv` per camera — one row "
            "per person per processed frame with the crop-quality verdict, the best "
            "face/ReID score and margin, the assigned name and the accumulated vote "
            "score. Use this to tune `face_thr`/`reid_thr` against real footage "
            "instead of guessing: it shows what a true match scored versus what a "
            "false positive scored on your own cameras."
        ),
    )
    session_date: Optional[str] = Field(
        None,
        description=(
            "Date (YYYY-MM-DD) whose daily body-fingerprint gallery to prefer during "
            "ReID matching (see `daily_gallery` docs) — normally the `session_date` "
            "returned by that day's `POST /checkin/video-multi` call. Defaults to "
            "today. Has no effect if no fingerprints exist yet for that date."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "video_paths": [
                    "/data/corridor_a.mp4",
                    "/data/office.mp4",
                    "/data/lobby.mp4",
                ],
                "camera_ids": ["corridor_a", "office_desks", "main_lobby"],
                "zones": [
                    [{"label": "corridor_a",   "zone_type": "common_area"}],
                    [{"label": "shared_desks", "zone_type": "work_area"}],
                    [{"label": "main_lobby",   "zone_type": "common_area"}],
                ],
                "stride": 2,
                "max_frames": 1500,
                "write_video": False,
            }
        }
    }


def _run_all(video_paths, camera_ids, **kwargs):
    """Sequential per-camera pipeline run. Returns merged events JSON."""
    progress_cb = kwargs.pop("progress", None)
    on_event_cb = kwargs.pop("on_event", None)
    per_video_zones = kwargs.pop("zones", None)
    # Per-camera threshold overrides are resolved here, per video, rather than
    # being baked into the shared kwargs — one camera's geometry must not
    # dictate another's. See EventsRequest.reid_thr_by_camera.
    thr_by_cam = kwargs.pop("reid_thr_by_camera", None) or {}
    margin_by_cam = kwargs.pop("reid_margin_by_camera", None) or {}
    det_conf_by_cam = kwargs.pop("det_conf_by_camera", None) or {}
    all_events = []
    annotated = []
    diagnostics = []
    n = len(video_paths)
    for i, (vp, cid) in enumerate(zip(video_paths, camera_ids)):
        def _p(done, total, _i=i, _n=n):
            if progress_cb is not None:
                progress_cb(int((_i + done / max(total, 1)) * 100), n * 100)
        video_zones = per_video_zones[i] if per_video_zones is not None else None
        per_camera = dict(kwargs)
        if cid in thr_by_cam:
            per_camera["reid_thr"] = thr_by_cam[cid]
        if cid in margin_by_cam:
            per_camera["reid_margin"] = margin_by_cam[cid]
        if cid in det_conf_by_cam:
            per_camera["det_conf"] = det_conf_by_cam[cid]
        # Per-camera counters, so "this camera produced 0 events" is
        # answerable from the result instead of requiring a re-run: it
        # separates "video never opened" from "nobody detected" from
        # "detected but not identified" from "identified but every event was
        # shorter than its min-duration". Camera 9 returning zero events with
        # no identity CSV at all was exactly this ambiguity.
        stats: dict = {}
        df, ann = run_pipeline(
            vp, camera_id=cid, progress=_p, zones=video_zones,
            on_event=on_event_cb, stats_out=stats, **per_camera,
        )
        diagnostics.append(stats)
        # Replace NaN/inf with None so the result is JSON-serialisable.
        records = df.where(df.notna(), other=None).to_dict(orient="records")
        all_events.extend(records)
        if ann is not None:
            annotated.append({"camera_id": cid, "path": f"/outputs/{ann.name}"})
    return {
        "events": all_events,
        "event_count": len(all_events),
        "annotated_videos": annotated,
        "diagnostics": diagnostics,
    }


@router.post(
    "/run",
    response_model=JobSubmitResponse,
    summary="Submit a pipeline job",
    response_description="Job ID to poll with GET /events/{job_id}.",
    status_code=202,
)
def run(req: EventsRequest):
    """Submit an async background job to run the full activity-tracking pipeline.

    Videos are processed **sequentially** (one camera at a time) to avoid
    GPU memory contention from the shared YOLO / InsightFace singletons.

    Returns immediately with a `job_id`. Poll `GET /events/{job_id}` until
    `status` is `done`, then read `result.events`.

    **Zone types and event detection:**

    | zone_type | Events detected |
    |-----------|----------------|
    | `work_area` | `presence`, `working`, `phone_use` |
    | `common_area` | `presence`, `interaction` |

    Each camera gets its own zone list — pass `null` for a camera to use the
    full frame as a single `work_area`.
    """
    for vp in req.video_paths:
        if not is_remote(vp) and not Path(vp).exists():
            raise HTTPException(400, f"Video not found: {vp}")
    if req.camera_ids and len(req.camera_ids) != len(req.video_paths):
        raise HTTPException(400, "camera_ids length must match video_paths length.")
    if req.zones is not None and len(req.zones) != len(req.video_paths):
        raise HTTPException(400, "zones length must match video_paths length.")

    camera_ids = req.camera_ids or [f"cam{i}" for i in range(len(req.video_paths))]
    # get_gallery() re-embeds the gallery if it was built with outdated ReID
    # preprocessing (see gallery.get_gallery) — a stale bank produces no
    # error, just a run where everyone comes back UNKNOWN.
    gallery = get_gallery()

    job = store.submit(
        _run_all,
        req.video_paths,
        camera_ids,
        gallery=gallery,
        zones=req.zones,
        det_conf=req.det_conf,
        det_iou=req.det_iou,
        face_thr=req.face_thr,
        reid_thr=req.reid_thr,
        reid_margin=req.reid_margin,
        reid_thr_by_camera=req.reid_thr_by_camera,
        reid_margin_by_camera=req.reid_margin_by_camera,
        det_conf_by_camera=req.det_conf_by_camera,
        fuse_win=req.fuse_win,
        stride=req.stride,
        max_frames=req.max_frames,
        prox_px=req.prox_px,
        write_video=req.write_video,
        annotate_stride=req.annotate_stride,
        session_date=req.session_date,
        debug_identity=req.debug_identity,
    )
    return {"job_id": job.id, "status": job.status}


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll a pipeline job",
    response_description="Current job status and result when done.",
)
def get_job(job_id: str):
    """Fetch the current status of a submitted pipeline job.

    Poll this endpoint every few seconds until `status` is `done` or `error`.

    - `running` — pipeline is processing videos.
    - `done`    — `result.events` contains the full event list.
    - `error`   — `error` field contains the exception message.
    """
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, f"Unknown job_id: {job_id}")
    return job.to_dict()
