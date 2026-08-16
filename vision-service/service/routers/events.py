"""POST /events/run + GET /events/{job_id} — async pipeline job + results."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import (
    DEFAULT_DET_CONF, DEFAULT_DET_IOU, DEFAULT_FACE_THR, DEFAULT_FUSE_WIN,
    DEFAULT_MAX_FRAMES, DEFAULT_PROX_PX, DEFAULT_REID_THR, DEFAULT_STRIDE, GALLERY_PATH,
)
from ..gallery import EmployeeGallery
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
        description="If true, save an annotated MP4 to `outputs/`. Path is returned in the result.",
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
    per_video_zones = kwargs.pop("zones", None)
    all_events = []
    annotated = []
    n = len(video_paths)
    for i, (vp, cid) in enumerate(zip(video_paths, camera_ids)):
        def _p(done, total, _i=i, _n=n):
            if progress_cb is not None:
                progress_cb(int((_i + done / max(total, 1)) * 100), n * 100)
        video_zones = per_video_zones[i] if per_video_zones is not None else None
        df, ann = run_pipeline(vp, camera_id=cid, progress=_p, zones=video_zones, **kwargs)
        # Replace NaN/inf with None so the result is JSON-serialisable.
        records = df.where(df.notna(), other=None).to_dict(orient="records")
        all_events.extend(records)
        if ann is not None:
            annotated.append(str(ann))
    return {
        "events": all_events,
        "event_count": len(all_events),
        "annotated_videos": annotated,
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
    gallery = EmployeeGallery.load(GALLERY_PATH) if GALLERY_PATH.exists() else None

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
        fuse_win=req.fuse_win,
        stride=req.stride,
        max_frames=req.max_frames,
        prox_px=req.prox_px,
        write_video=req.write_video,
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
