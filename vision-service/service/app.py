"""FastAPI app entrypoint.

Run with:
    venv/bin/python -m uvicorn service.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .routers import checkin, enroll, events, files

_DESCRIPTION = """
End-to-end employee activity tracking over surveillance video.

## Typical workflow

1. **Enroll** employees once — provide face and/or body photos.
2. **Checkin** to identify a person at the door from a single image or camera clip.
3. **Run the pipeline** over NVR footage to extract structured activity events.
4. **Poll** the job until complete and read the events.

## Source formats

All endpoints that accept a file path also accept:
- `https://` or `http://` URLs — the service downloads the file automatically.
- `s3://bucket/key` URIs — requires `boto3` and AWS credentials on the server.
- RTSP stream URLs (`rtsp://...`) — supported by `/checkin/video`.
- Direct upload via `POST /files/upload` → use the returned path in subsequent calls.

## Event types

| Event | Trigger |
|-------|---------|
| `presence` | Person detected in a zone for ≥ 2 s |
| `working` | Person at desk with both laptop and monitor for ≥ 3 s |
| `phone_use` | Person's bounding box overlaps a detected phone for ≥ 2 s |
| `interaction` | Two employees within proximity threshold in a `common_area` for ≥ 4 s |
"""

_TAGS = [
    {
        "name": "enroll",
        "description": "Register employees into the identity gallery from face and/or body photos.",
    },
    {
        "name": "checkin",
        "description": (
            "Identify a person from a **single image** or a **video / camera stream**. "
            "The video endpoint applies motion and blur gates to skip useless frames "
            "before running any expensive model inference."
        ),
    },
    {
        "name": "events",
        "description": (
            "Submit an async background job that runs the full activity-tracking pipeline "
            "over one or more videos. Returns a `job_id` immediately — poll "
            "`GET /events/{job_id}` until `status` is `done`."
        ),
    },
    {
        "name": "files",
        "description": (
            "Upload a video or image directly to the server. "
            "The returned `path` can be used in any other endpoint."
        ),
    },
]

app = FastAPI(
    title="Employee Activity Tracking",
    description=_DESCRIPTION,
    version="0.2.0",
    openapi_tags=_TAGS,
    contact={"name": "Omar Khairat"},
    license_info={"name": "Research prototype — not for production use"},
)

app.include_router(enroll.router)
app.include_router(checkin.router)
app.include_router(events.router)
app.include_router(files.router)


@app.get("/health", tags=["health"], summary="Service health check")
def health():
    """Returns `{\"status\": \"ok\"}` when the service is up and accepting requests."""
    return {"status": "ok"}
