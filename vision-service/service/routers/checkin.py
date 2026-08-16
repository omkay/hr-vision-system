"""POST /checkin — identify the person in an image or video via face/ReID match."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import DEFAULT_FACE_THR, DEFAULT_REID_THR, GALLERY_PATH
from ..gallery import EmployeeGallery
from ..pipeline import checkin, checkin_video
from ..schemas import CheckinResponse, CheckinVideoResponse
from ..storage import is_remote, _is_stream

router = APIRouter(prefix="/checkin", tags=["checkin"])


class CheckinRequest(BaseModel):
    """Identify the person in a single image."""

    image_path: str = Field(
        ...,
        description=(
            "Path or URL to the image. "
            "Accepts a local server path, an `https://` URL, or an `s3://` URI."
        ),
        examples=["/data/frames/entrance_14s.jpg"],
    )
    face_thr: float = Field(
        DEFAULT_FACE_THR,
        description="Min cosine similarity for a face match (0–1). Lower = more matches, higher = stricter.",
    )
    reid_thr: float = Field(
        DEFAULT_REID_THR,
        description="Min cosine similarity for a ReID (body) match (0–1).",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "image_path": "/data/frames/entrance_14s.jpg",
                "face_thr": 0.45,
                "reid_thr": 0.75,
            }
        }
    }


class CheckinVideoRequest(BaseModel):
    """Identify the person in a video file or live camera stream."""

    source: str = Field(
        ...,
        description=(
            "Video source — any of: "
            "local file path, `https://` URL, `s3://` URI, "
            "`rtsp://` stream URL, or webcam index string (e.g. `\"0\"`)."
        ),
        examples=[
            "/data/checkin_clip.mp4",
            "rtsp://192.168.1.100:554/stream1",
            "https://cdn.example.com/clip.mp4",
        ],
    )
    face_thr: float = Field(
        DEFAULT_FACE_THR,
        description="Min cosine similarity for a face match (0–1).",
    )
    reid_thr: float = Field(
        DEFAULT_REID_THR,
        description="Min cosine similarity for a ReID (body) match (0–1).",
    )
    stride: int = Field(
        5,
        description="Process every Nth frame. Higher = faster but may miss brief appearances.",
    )
    motion_thr: float = Field(
        0.013,
        description=(
            "Minimum fraction of pixels that must change between frames for the frame to be processed. "
            "Frames below this threshold are skipped as static/empty. `0.013` = 1.3% of pixels. "
            "Tuned via a genetic algorithm against real footage — see GA optimisation/."
        ),
    )
    blur_thr: float = Field(
        183.0,
        description=(
            "Minimum Laplacian variance for a frame to be considered sharp enough to process. "
            "Lower = accepts blurrier frames. Tuned via a genetic algorithm against real footage "
            "at native resolution (previous default of 80.0 was undertuned — see GA optimisation/)."
        ),
    )
    early_exit_conf: float = Field(
        0.77,
        description=(
            "Stop scanning as soon as a match exceeds this confidence. "
            "Set to `1.1` to disable early exit and process up to `max_frames`. "
            "Tuned via GA: real face-match confidences on test footage rarely exceeded ~0.9, "
            "so requiring 0.90 forced scanning ~6.6x more frames than necessary for a ~1.5% "
            "coverage gain — see GA optimisation/."
        ),
    )
    max_frames: int = Field(
        500,
        description=(
            "Hard cap on frames to inspect. "
            "Acts as the processing window for live streams — e.g. `150` ≈ 5 s at 30 fps with stride 1."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "source": "/data/checkin_clip.mp4",
                "stride": 5,
                "motion_thr": 0.01,
                "blur_thr": 80.0,
                "early_exit_conf": 0.90,
                "max_frames": 500,
            }
        }
    }


def _load_gallery() -> EmployeeGallery:
    if not GALLERY_PATH.exists():
        raise HTTPException(400, "No gallery enrolled yet. Call POST /enroll first.")
    return EmployeeGallery.load(GALLERY_PATH)


@router.post(
    "",
    response_model=CheckinResponse,
    summary="Identify person from a single image",
    response_description="Best identity match with confidence score and method used.",
)
def checkin_endpoint(req: CheckinRequest):
    """Identify the most prominent person in a single image.

    **Matching pipeline:**
    1. InsightFace detects and embeds any faces in the image.
    2. The face embedding is compared to the gallery — if a match exceeds `face_thr`, return it.
    3. If no face is found, fall back to YOLO person detection → largest crop → OSNet ReID embedding.
    4. If no ReID match exceeds `reid_thr`, return `UNKNOWN`.
    """
    gallery = _load_gallery()
    if not is_remote(req.image_path) and not Path(req.image_path).exists():
        raise HTTPException(400, f"Image not found: {req.image_path}")
    try:
        return checkin(req.image_path, gallery,
                       face_thr=req.face_thr, reid_thr=req.reid_thr)
    except (FileNotFoundError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.post(
    "/video",
    response_model=CheckinVideoResponse,
    summary="Identify person from a video or stream",
    response_description="Best identity match with vote tally and frame filter statistics.",
)
def checkin_video_endpoint(req: CheckinVideoRequest):
    """Identify the most prominent person across a video file or live camera stream.

    **Three-stage filter funnel** — only frames that pass all gates reach the models:

    | Stage | Cost | What it skips |
    |-------|------|--------------|
    | Motion gate | ~0 ms | Static / empty frames |
    | Blur gate | ~1 ms | Motion-blurred or out-of-focus frames |
    | Face + ReID | ~50 ms | Frames where no person or face is visible |

    **Early exit** — once a match exceeds `early_exit_conf`, the scan stops immediately.

    **Vote aggregation** — results are accumulated across all matching frames;
    the identity with the most votes (ties broken by confidence) is returned.

    **Measured performance** on a 113 s / 3402-frame test clip:
    motion gate eliminated 74% of frames; with early exit the correct identity
    was returned after reading only 12.7% of the video in **4.3 seconds**.
    """
    gallery = _load_gallery()
    if not is_remote(req.source) and not _is_stream(req.source):
        if not Path(req.source).exists():
            raise HTTPException(400, f"Video source not found: {req.source}")
    try:
        return checkin_video(
            req.source, gallery,
            face_thr=req.face_thr,
            reid_thr=req.reid_thr,
            stride=req.stride,
            motion_thr=req.motion_thr,
            blur_thr=req.blur_thr,
            early_exit_conf=req.early_exit_conf,
            max_frames=req.max_frames,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
