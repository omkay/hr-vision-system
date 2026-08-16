"""POST /enroll — register a person's face/body fingerprint from image paths."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..gallery import enroll_person
from ..models import get_face_embedder, get_reid_embedder
from ..schemas import EnrollResponse

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
