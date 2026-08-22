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
    skipped_sources: List[str] = Field(
        default_factory=list,
        description=(
            "Images that could not be fetched and were skipped. Enrollment no longer "
            "fails wholesale when one source is dead — an upstream photo row can "
            "outlive its file, and one broken URL used to abort the whole employee's "
            "enrollment. Also compare `face_embeddings_used` against "
            "`face_images_copied`: a copied photo with no detectable face contributes "
            "nothing, and an employee with zero usable face embeddings can never be "
            "face-identified at checkin."
        ),
    )
    total_employees: int = Field(..., description="Total number of employees now in the gallery.")


class DeleteEnrollmentResponse(BaseModel):
    """What was removed when deleting an employee's enrollment."""
    name: str = Field(..., description="Employee ID that was deleted.")
    images_removed: bool = Field(
        ..., description="Whether `gallery/<name>/` existed and was deleted.",
    )
    gallery_entry_removed: bool = Field(
        ..., description="Whether the employee had an entry in gallery.npz.",
    )
    daily_fingerprint_dates_cleared: List[str] = Field(
        default_factory=list,
        description=(
            "Dates (YYYY-MM-DD) whose daily body-fingerprint file contained this "
            "employee and was rewritten without them. Empty when they had no "
            "fingerprints on record."
        ),
    )


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
    fingerprinted: List[str] = Field(
        default_factory=list,
        description=(
            "Employees whose daily body fingerprint was actually saved from this scan. "
            "Narrower than `matches` on purpose: only tracks confirmed by a real face "
            "match, with quality-passing crops, are allowed to seed the day's ReID "
            "reference — a body-only guess written here would propagate to every zone "
            "camera for the rest of the day. If an employee appears in `matches` but "
            "not here, zone cameras will fall back to the enrollment gallery for them."
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


class CameraDiagnostics(BaseModel):
    """Per-camera counters explaining how a run reached its event count.

    "This camera produced no events" has at least six distinct causes, each
    with a different fix: the video never opened, no frames were read, nobody
    was detected, people were detected but never tracked, tracked but never
    identified, or identified but every event fell under its min-duration
    threshold. These fields separate them without a re-run.
    """
    camera_id: str
    frames_read: int = Field(..., description="Frames pulled from the video (before stride).")
    frames_processed: int = Field(..., description="Frames actually run through detection.")
    video_fps: float = Field(..., description="Source frame rate as reported by the container.")
    person_detections: int = Field(
        ...,
        description=(
            "Total person detections with a track ID across all frames. **Zero here "
            "means detection or tracking failed** — identity, thresholds and zones are "
            "all downstream and can't be the cause."
        ),
    )
    quality_rejected_crops: int = Field(
        ..., description="Crops dropped by the quality gate before matching (quality.py)."
    )
    distinct_tracks: int = Field(..., description="Distinct track IDs seen.")
    identified_employees: List[str] = Field(
        default_factory=list, description="Employees committed to at least one track."
    )
    last_frame_with_a_person: int = Field(
        ...,
        description=(
            "Last processed frame index containing any person, or -1. Compare against "
            "`frames_processed`: a large gap means detections stopped partway through "
            "the video rather than the video being short."
        ),
    )
    events_after_min_duration: int = Field(
        ...,
        description=(
            "Events surviving EventEngine's min-duration filter (presence 2s, phone 2s, "
            "working 3s, interaction 4s). Non-zero detections with zero events here "
            "means people were seen too briefly, not missed."
        ),
    )
    identity_enabled: bool = Field(
        ..., description="False when no gallery was loaded — every event would be UNKNOWN."
    )


class JobResultPayload(BaseModel):
    events: List[EventRecord]
    event_count: int = Field(..., description="Total number of events returned.")
    diagnostics: List[CameraDiagnostics] = Field(
        default_factory=list,
        description="One entry per camera — see CameraDiagnostics.",
    )
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
    partial_events: List[EventRecord] = Field(
        default_factory=list,
        description=(
            "Events finalized so far, updated live while the job is still "
            "`running` — appended to only, never rewritten, so callers can "
            "poll repeatedly and just look at new entries past the length "
            "they already saw. By the time `status` is `done` this already "
            "equals `result.events` in content (order may differ)."
        ),
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
