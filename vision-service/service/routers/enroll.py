"""POST /enroll — register a person's face/body fingerprint from image paths."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..gallery import delete_person, enroll_person
from ..models import get_face_embedder, get_reid_embedder
from ..schemas import DeleteEnrollmentResponse, EnrollResponse

router = APIRouter(prefix="/enroll", tags=["enroll"])


class EnrollRequest(BaseModel):
    """Images to enroll for a single employee. Provide at least one face or body image."""

    name: str = Field(
        ...,
        description="Unique employee ID. Re-enrolling the same name replaces their embeddings.",
        examples=["hasan", "majd"],
    )
    face_images: List[str] = Field(
        default_factory=list,
        description=(
            "Paths or URLs to face photos. More images = more robust matching. "
            "Accepts local paths, `https://` URLs, or `s3://` URIs."
        ),
    )
    body_images: List[str] = Field(
        default_factory=list,
        description=(
            "Paths or URLs to full-body photos used for ReID fallback "
            "when the face is not visible. "
            "Accepts local paths, `https://` URLs, or `s3://` URIs."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "hasan",
                "face_images": [
                    "/data/gallery/hasan/face/01.jpg",
                    "/data/gallery/hasan/face/02.jpg",
                ],
                "body_images": [
                    "/data/gallery/hasan/body/01.jpg",
                ],
            }
        }
    }


@router.post(
    "",
    response_model=EnrollResponse,
    summary="Register an employee",
    response_description="Number of embeddings successfully stored.",
)
def enroll(req: EnrollRequest):
    """Register or update an employee in the identity gallery.

    - Accepts face images, body images, or both.
    - Re-enrolling the same `name` **replaces** their existing embeddings.
    - The gallery is persisted to `gallery/gallery.npz` and reused by all
      subsequent `/checkin` and `/events/run` calls.
    """
    if not req.face_images and not req.body_images:
        raise HTTPException(400, "Provide at least one face or body image path.")
    try:
        summary = enroll_person(
            req.name, req.face_images, req.body_images,
            get_face_embedder(), get_reid_embedder(),
        )
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e))
    return summary


@router.delete(
    "/{name}",
    response_model=DeleteEnrollmentResponse,
    summary="Delete an employee's enrollment and fingerprints",
    response_description="What was actually removed.",
)
def delete_enrollment(name: str):
    """Remove an employee from the identity gallery entirely.

    Call this when the employee is deleted upstream (Hr_SmartPay /
    backend-service `DELETE /employees/delete/{id}` does this automatically).
    Three stores are cleaned, and all three matter:

    - `gallery/<name>/` enrollment images — otherwise any gallery rebuild
      silently re-enrolls the deleted employee from disk.
    - their entry in `gallery.npz` — the banks used for matching.
    - their body fingerprints in `gallery/daily/*.npz`, for **every** date on
      record. This is the one that bites: `match_reid` prefers the daily bank
      over enrollment, so a deleted employee's fingerprint would keep winning
      matches and attributing activity events to an ID that no longer
      resolves to anyone.

    **Idempotent** — deleting someone who was never enrolled returns 200 with
    everything reported as `false`, not an error, since an employee record can
    exist upstream without ever having had photos uploaded.
    """
    try:
        return delete_person(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
