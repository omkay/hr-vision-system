"""Shared Pydantic schemas — request bodies, response models, and zone definitions."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ── Zone definition (request) ─────────────────────────────────────────────────

class ZoneDefinition(BaseModel):
    """A named, typed region of a video frame.

    Coordinates are in pixels from the top-left corner of the frame.
    Omit all four coordinates (or set to null) to cover the entire frame.

    **zone_type** controls which events are detected inside this zone:
    - `work_area`   → `presence`, `working` (laptop + monitor), `phone_use`
    - `common_area` → `presence`, `interaction` (proximity between employees)
    """

    label: str = Field(
        ...,
        description="Human-readable label attached to every event from this zone.",
        examples=["shared_desks", "corridor_a", "main_lobby"],
    )
    zone_type: Literal["work_area", "common_area"] = Field(
        "work_area",
        description="`work_area` tracks desk activity; `common_area` tracks movement and interactions.",
    )
    x1: Optional[int] = Field(None, description="Left edge in pixels. Defaults to 0.")
    y1: Optional[int] = Field(None, description="Top edge in pixels. Defaults to 0.")
    x2: Optional[int] = Field(None, description="Right edge in pixels. Defaults to frame width.")
    y2: Optional[int] = Field(None, description="Bottom edge in pixels. Defaults to frame height.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"label": "shared_desks", "zone_type": "work_area"},
                {"label": "corridor_a",   "zone_type": "common_area"},
                {"label": "reception",    "zone_type": "common_area",
                 "x1": 0, "y1": 800, "x2": 1920, "y2": 1080},
            ]
        }
    }


# ── Enroll ────────────────────────────────────────────────────────────────────

class EnrollResponse(BaseModel):
    """Summary of embeddings registered for an employee."""
    name: str = Field(..., description="Employee ID that was enrolled.")
    face_images_copied: int = Field(..., description="Number of face image files copied into the gallery.")
    body_images_copied: int = Field(..., description="Number of body image files copied into the gallery.")
    face_embeddings_used: int = Field(..., description="Number of face embeddings actually computed and stored.")
    body_embeddings_used: int = Field(..., description="Number of body (ReID) embeddings actually computed and stored.")
    total_employees: int = Field(..., description="Total number of employees now in the gallery.")


# ── Checkin ───────────────────────────────────────────────────────────────────

class CheckinResponse(BaseModel):
    """Result of a single-image identity lookup."""
    employee_id: str = Field(
        ...,
        description="Matched employee ID, or `UNKNOWN` if no match exceeded the threshold.",
    )
    confidence: float = Field(..., description="Cosine similarity score of the best match (0–1).")
    method: Literal["face", "reid", "none"] = Field(
        ...,
        description=(
            "`face` — matched via InsightFace ArcFace embedding. "
            "`reid` — face not found; matched via OSNet body embedding. "
            "`none` — no person detected or no match above threshold."
        ),
    )


class CheckinVideoResponse(BaseModel):
    """Result of a video/stream identity lookup with per-frame filter statistics."""
    employee_id: str = Field(
        ..., description="Matched employee ID, or `UNKNOWN` if no match was found."
    )
    confidence: float = Field(..., description="Confidence of the best match (0–1).")
    method: Literal["face", "reid", "none"] = Field(..., description="How the match was made.")
    votes: Optional[Dict[str, int]] = Field(
        None,
        description="Vote tally across all matched frames — `{employee_id: frame_count}`.",
    )
    frames_read: int = Field(..., description="Total frames pulled from the source.")
    frames_processed: int = Field(..., description="Frames that passed all gates and ran inference.")
    skipped_motion: int = Field(..., description="Frames dropped by the motion gate (static scene).")
    skipped_blur: int = Field(..., description="Frames dropped by the blur gate (blurry/motion blur).")
    skipped_no_face: int = Field(..., description="Frames that passed gates but had no detectable person.")


class CheckinVideoMatch(BaseModel):
    """One distinct identified person from a multi-person checkin scan."""
    employee_id: str = Field(..., description="Matched employee ID.")
    confidence: float = Field(..., description="Best face-match confidence (0-1) seen for this person's track.")


class CheckinVideoMultiResponse(BaseModel):
    """Result of scanning a video for every distinct person, not just the most prominent one.

    Use this instead of /checkin/video when a clip may contain more than one
    person you need to identify (e.g. several employees passing the same
    camera) — /checkin/video's early exit stops at the first confident match
    and would miss anyone appearing later in the clip.
    """
    matches: List[CheckinVideoMatch] = Field(
        ..., description="One entry per distinct employee identified (UNKNOWN/uncommitted tracks excluded)."
    )
    num_tracks: int = Field(..., description="Number of distinct tracked people that reached a committed identity.")
    session_date: str = Field(
        ...,
        description=(
            "Date (YYYY-MM-DD) this checkin's daily body fingerprints were saved under. "
            "Pass this same value as `session_date` on `POST /events/run` for any zone "
            "videos from the same day, so they match against today's fresh appearance "
            "instead of the static enrollment gallery — see IdentityFuser.match_reid."
        ),
    )
    frames_read: int = Field(..., description="Total frames pulled from the source.")
    frames_processed: int = Field(..., description="Frames actually run through detection/tracking (after stride).")


# ── Events ────────────────────────────────────────────────────────────────────

class JobSubmitResponse(BaseModel):
    """Returned immediately when a pipeline job is submitted."""
    job_id: str = Field(
        ..., description="Unique job identifier — poll with `GET /events/{job_id}`."
    )
    status: Literal["pending", "running", "done", "error"] = Field(
        ..., description="Current job status."
    )


class EventRecord(BaseModel):
    """A single detected activity event."""
    camera_id: str = Field(..., description="Camera label supplied in the request.")
    employee_id: str = Field(..., description="Identified employee, or `UNKNOWN`.")
    event_type: Literal["presence", "working", "phone_use", "interaction"] = Field(
        ...,
        description=(
            "`presence` — person in zone. "
            "`working` — person at desk with laptop + monitor. "
            "`phone_use` — person holding a phone. "
            "`interaction` — two employees within proximity threshold."
        ),
    )
    start_s: float = Field(..., description="Event start time in seconds from video start.")
    end_s: float = Field(..., description="Event end time in seconds.")
    duration_s: float = Field(..., description="Event duration in seconds.")
    zone: Optional[str] = Field(None, description="Zone label where the event occurred.")
    zone_type: Optional[Literal["work_area", "common_area"]] = Field(
        None, description="Zone type where the event occurred."
    )
    work_proxy: Optional[str] = Field(
        None, description="For `working` events: device combination detected, e.g. `laptop+monitor`."
    )
    peers: Optional[List[str]] = Field(
        None, description="For `interaction` events: the two employee IDs involved."
    )


class AnnotatedVideoRef(BaseModel):
    """One annotated debug video, mapped back to the camera it came from."""
    camera_id: str = Field(..., description="Camera label this video was generated from — matches `camera_ids` in the request.")
    path: str = Field(
        ...,
        description=(
            "Path under this service's `/outputs` static route, e.g. `/outputs/foo_annotated.mp4`. "
            "Fetch it at `<this service's base URL><path>` — callers behind another layer "
            "(e.g. Hr_SmartPay) should prefix with whatever base URL reaches this service from "
            "a browser, which may differ from the URL they use for server-to-server calls."
        ),
    )


class JobResultPayload(BaseModel):
    events: List[EventRecord]
    event_count: int = Field(..., description="Total number of events returned.")
    annotated_videos: List[AnnotatedVideoRef] = Field(
        default_factory=list,
        description="One entry per camera with bounding boxes/zones/labels drawn in — only populated when `write_video=true`.",
    )


class JobStatusResponse(BaseModel):
    """Full job status — poll until `status` is `done` or `error`."""
    job_id: str
    status: Literal["pending", "running", "done", "error"]
    progress: Optional[float] = Field(
        None, description="Completion fraction 0.0–1.0 while the job is running."
    )
    error: Optional[str] = Field(None, description="Error message when status is `error`.")
    created_at: Optional[float] = Field(None, description="Unix timestamp when the job was created.")
    started_at: Optional[float] = Field(None, description="Unix timestamp when processing began.")
    finished_at: Optional[float] = Field(None, description="Unix timestamp when the job completed.")
    result: Optional[Any] = Field(
        None, description="Job result payload — present when `status` is `done`."
    )


# ── Files ─────────────────────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    """Confirmation of a successful file upload."""
    path: str = Field(
        ...,
        description=(
            "Absolute server-side path of the saved file. "
            "Pass this directly to `/checkin`, `/checkin/video`, or `/events/run`."
        ),
    )
    filename: str = Field(..., description="Original filename as uploaded.")
    size_bytes: int = Field(..., description="File size in bytes.")
