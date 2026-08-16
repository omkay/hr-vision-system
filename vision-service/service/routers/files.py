"""POST /files/upload — upload a video or image directly to the server."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..config import DATA_DIR
from ..schemas import UploadResponse

router = APIRouter(prefix="/files", tags=["files"])

# Allowed extensions — expand as needed
ALLOWED_SUFFIXES = {
    ".mp4", ".avi", ".mov", ".mkv",   # video
    ".jpg", ".jpeg", ".png", ".bmp",  # image
}


@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="Upload a video or image",
    response_description="Server-side path of the saved file.",
    status_code=201,
)
async def upload_file(file: UploadFile = File(..., description="Video or image file to upload.")):
    """Upload a video or image to the server's `data/` directory.

    Use this when you have the file bytes and cannot provide a URL or S3 URI.
    The returned `path` can be passed directly to any endpoint that accepts a source:

    ```
    POST /files/upload          → { "path": "/data/upload_abc123.mp4" }
    POST /checkin/video         → { "source": "/data/upload_abc123.mp4", ... }
    POST /events/run            → { "video_paths": ["/data/upload_abc123.mp4"], ... }
    ```

    **Supported formats:** `.mp4`, `.avi`, `.mov`, `.mkv`, `.jpg`, `.jpeg`, `.png`, `.bmp`
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            400,
            f"Unsupported file type '{suffix}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_SUFFIXES))}",
        )

    dest = DATA_DIR / f"upload_{uuid.uuid4().hex}{suffix}"
    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as e:
        raise HTTPException(500, f"Failed to save file: {e}")

    return UploadResponse(
        path=str(dest),
        filename=file.filename or dest.name,
        size_bytes=dest.stat().st_size,
    )
