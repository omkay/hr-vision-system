"""POST /checkin — identify the person in an image or video via face/ReID match."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import (
    DEFAULT_DET_CONF, DEFAULT_DET_IOU, DEFAULT_FACE_THR, DEFAULT_MAX_FRAMES,
    DEFAULT_REID_THR, DEFAULT_STRIDE, GALLERY_PATH,
)
from ..gallery import EmployeeGallery, get_gallery
from ..pipeline import checkin, checkin_video, checkin_video_multi
from ..schemas import CheckinResponse, CheckinVideoMultiResponse, CheckinVideoResponse
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
    # get_gallery() (not EmployeeGallery.load) so a gallery embedded with
    # outdated ReID preprocessing is re-embedded instead of silently
    # depressing every similarity score — see gallery.get_gallery().
    gallery = get_gallery()
    if gallery is None:
        raise HTTPException(400, "No gallery enrolled yet. Call POST /enroll first.")
    return gallery


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


class CheckinVideoMultiRequest(BaseModel):
    """Identify every distinct person in a video file or live camera stream."""

    source: str = Field(
        ...,
        description=(
            "Video source — any of: local file path, `https://` URL, `s3://` URI, "
            "`rtsp://` stream URL, or webcam index string (e.g. `\"0\"`)."
        ),
        examples=["/data/lobby_camera.mp4"],
    )
    face_thr: float = Field(DEFAULT_FACE_THR, description="Min cosine similarity for a face match (0-1).")
    reid_thr: float = Field(DEFAULT_REID_THR, description="Min cosine similarity for a ReID (body) match (0-1).")
    stride: int = Field(
        DEFAULT_STRIDE,
        description=(
            "Process every Nth frame. Uses the zone/events pipeline's default "
            "(2), not checkin/video's — ByteTrack needs closer-together frames "
            "to keep a person's track_id stable, unlike single-match checkin."
        ),
    )
    max_frames: int = Field(
        DEFAULT_MAX_FRAMES,
        description="Hard cap on frames to inspect. No early exit here, so this is the real processing budget.",
    )
    det_conf: float = Field(DEFAULT_DET_CONF, description="YOLOv8 person-detection confidence threshold.")
    det_iou: float = Field(DEFAULT_DET_IOU, description="YOLOv8 NMS IoU threshold.")
    session_date: Optional[str] = Field(
        None,
        description=(
            "Date (YYYY-MM-DD) to save this scan's daily body fingerprints under — "
            "see the `daily_gallery` docs. Defaults to today. Pass the same value "
            "to `POST /events/run`'s `session_date` for zone videos from this same "
            "day so they benefit from today's fresh appearance during ReID matching."
        ),
    )

    debug_identity: bool = Field(
        False,
        description=(
            "If true, write `outputs/checkin_<video>_identity_debug.csv` — one row per "
            "person per processed frame with the crop-quality verdict, face/ReID score "
            "and margin, assigned name and vote score. Start here when zone cameras "
            "return UNKNOWN: this scan is where the face is supposed to be recognised "
            "and where the day's body fingerprints come from, so a failure here "
            "explains every downstream failure."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {"source": "/data/lobby_camera.mp4"},
        }
    }


@router.post(
    "/video-multi",
    response_model=CheckinVideoMultiResponse,
    summary="Identify every distinct person from a video or stream",
    response_description="One match per distinct person identified, not just the most prominent one.",
)
def checkin_video_multi_endpoint(req: CheckinVideoMultiRequest):
    """Identify every distinct person appearing in a video — not just one.

    Use this instead of `/checkin/video` whenever a clip might show more than
    one person you need to identify (e.g. several employees passing the same
    entrance camera at different times). `/checkin/video` is built for a
    kiosk "one person walks up" scenario and its early exit stops scanning
    the instant the *first* confident match is found — correct for that use
    case, wrong here, since anyone appearing later in the clip would never
    even be looked at.

    **How it differs from `/checkin/video`:**

    | | `/checkin/video` | `/checkin/video-multi` |
    |---|---|---|
    | Goal | fastest single answer | complete list of everyone identified |
    | Tracking | none (independent frame votes) | ByteTrack, same as `/events/run` |
    | Early exit | yes, on first confident match | no — scans the full video/`max_frames` |
    | Result | one `employee_id` | list of `{employee_id, confidence}` |

    Each detected person is tracked across frames (ByteTrack) and resolved to
    an identity via the same rolling-vote `IdentityFuser` the zone/events
    pipeline uses — a track only appears in the result once it has
    accumulated enough consistent evidence to "commit" to an identity, so a
    single stray misdetection can't show up as "this person was here".
    """
    gallery = _load_gallery()
    if not is_remote(req.source) and not _is_stream(req.source):
        if not Path(req.source).exists():
            raise HTTPException(400, f"Video source not found: {req.source}")
    try:
        return checkin_video_multi(
            req.source, gallery,
            face_thr=req.face_thr,
            reid_thr=req.reid_thr,
            stride=req.stride,
            max_frames=req.max_frames,
            det_conf=req.det_conf,
            det_iou=req.det_iou,
            session_date=req.session_date,
            debug_identity=req.debug_identity,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
