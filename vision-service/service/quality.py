"""Person-crop quality assessment — the gate in front of identity matching.

Both of the identity failures this module exists to address came from feeding
a crop into face/ReID matching that should never have been trusted:

* A person at the extreme edge of the check-in camera's frame — body truncated
  by the frame border, back of the head to the lens, smeared by motion blur.
  OSNet still produces a 512-d vector for that, `max(bank @ v)` still picks a
  nearest neighbour, and (before this gate) that nearest neighbour became five
  ReID votes and a permanently committed employee name.
* Very small/distant crops, where the embedding is dominated by clothing colour
  averages and generic body shape rather than anything person-specific.

Two severities are distinguished, because "reject everything imperfect" would
push the system straight back into the UNKNOWN-everywhere failure mode that
DEFAULT_REID_THR's history (see config.py) already went through once:

* **Hard reject** (`ok=False`) — the crop is unusable. The caller must not vote
  at all on this frame: not a name, and not UNKNOWN either. A truncated sliver
  is not evidence of absence any more than it is evidence of presence.
* **Penalty** (`ok=True`, `penalty > 0`) — the crop is usable but degraded, so
  the similarity thresholds it must clear are raised by `penalty`. A borderline
  0.66 match on a sharp, fully-visible, close-up crop is believable; the same
  0.66 on a blurry edge-clipped one is not.
"""
from __future__ import annotations

import dataclasses
from typing import List, Sequence, Tuple

import cv2
import numpy as np

from .config import (
    IDENTITY_ASPECT_HARD_RANGE,
    IDENTITY_BLUR_HARD, IDENTITY_BLUR_SOFT, IDENTITY_EDGE_MARGIN_PX,
    IDENTITY_MIN_CROP_H, IDENTITY_QUALITY_PENALTY, IDENTITY_SOFT_CROP_H,
)


@dataclasses.dataclass
class CropQuality:
    """Verdict on one person crop.

    ok:      False → do not match, do not vote (hard reject).
    penalty: added to face_thr / reid_thr for this crop only.
    reasons: short tags for the debug log, e.g. ["blur_soft", "edge_clipped"].
    """

    ok: bool
    penalty: float
    reasons: List[str]
    height: int
    aspect: float
    blur: float
    edge_clipped: bool

    @property
    def reason_str(self) -> str:
        return "|".join(self.reasons) if self.reasons else "clean"


def _touches_frame_edge(bbox: Sequence[int], frame_w: int, frame_h: int,
                        margin: int = IDENTITY_EDGE_MARGIN_PX) -> bool:
    x1, y1, x2, y2 = (int(v) for v in bbox)
    return (x1 <= margin or y1 <= margin
            or x2 >= frame_w - margin or y2 >= frame_h - margin)


def assess_crop(crop: np.ndarray, bbox: Sequence[int],
                frame_w: int, frame_h: int) -> CropQuality:
    """Judge whether *crop* is worth running identity models on.

    bbox / frame_w / frame_h are needed on top of the crop itself because
    frame-border truncation is invisible from the pixels alone — a person
    whose legs are cut off by the bottom of the frame looks like a perfectly
    ordinary crop until you notice the box ends exactly at y = frame_h.
    """
    reasons: List[str] = []
    penalty = 0.0

    if crop is None or crop.size == 0:
        return CropQuality(False, 0.0, ["empty"], 0, 0.0, 0.0, False)

    h, w = crop.shape[:2]
    aspect = h / max(w, 1)
    edge_clipped = _touches_frame_edge(bbox, frame_w, frame_h)

    # Blur — Laplacian variance, the same cheap sharpness measure
    # checkin_video()'s stage-2 gate uses, applied here per-crop rather than
    # per-frame (a sharp frame can still contain one motion-smeared person).
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # ── Hard rejects ─────────────────────────────────────────────────────────
    if h < IDENTITY_MIN_CROP_H:
        reasons.append("too_small")
    lo_hard, hi_hard = IDENTITY_ASPECT_HARD_RANGE
    if not (lo_hard <= aspect <= hi_hard):
        # Only genuinely impossible boxes: a wide band (two people merged
        # into one detection) or an implausibly thin column. A near-square
        # crop is NORMAL on a steeply-angled ceiling camera — see the range's
        # comment in config.py for what assuming otherwise cost.
        reasons.append("aspect_invalid")
    if blur < IDENTITY_BLUR_HARD:
        reasons.append("blur_hard")

    if reasons:
        return CropQuality(False, 0.0, reasons, h, aspect, blur, edge_clipped)

    # ── Graduated penalties ──────────────────────────────────────────────────
    if edge_clipped:
        reasons.append("edge_clipped")
        penalty += IDENTITY_QUALITY_PENALTY
    if h < IDENTITY_SOFT_CROP_H:
        reasons.append("small")
        penalty += IDENTITY_QUALITY_PENALTY
    if blur < IDENTITY_BLUR_SOFT:
        reasons.append("blur_soft")
        penalty += IDENTITY_QUALITY_PENALTY

    return CropQuality(True, penalty, reasons, h, aspect, blur, edge_clipped)


def normalize_illumination(crop: np.ndarray) -> np.ndarray:
    """Gray-world white balance + CLAHE on the luma channel.

    Motivated by the backlit-entrance case: an employee walking IN from a
    glass door is lit completely differently from the same employee walking
    OUT, and OSNet embeddings are sensitive enough to that colour/exposure
    shift to drop a true match below threshold. Normalising both the gallery
    crops and the query crops the same way removes most of the gap.

    Applied to ReID inputs only — InsightFace does its own preprocessing and
    is far less colour-sensitive, and re-tinting a face crop risks making
    things worse rather than better.
    """
    if crop is None or crop.size == 0:
        return crop
    img = crop.astype(np.float32)
    means = img.reshape(-1, 3).mean(axis=0) + 1e-6
    img *= float(means.mean()) / means          # gray-world: equalise channel means
    img = np.clip(img, 0, 255).astype(np.uint8)

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def topk_similarity(bank: np.ndarray, vec: np.ndarray, k: int) -> float:
    """Mean of the *k* highest cosine similarities between *vec* and *bank*.

    Replaces the previous `float(np.max(bank @ vec))`. `max` over a bank of
    N reference vectors is the most overfitting-prone statistic available:
    it only takes ONE unlucky reference crop (a bad enrollment photo, a
    frame where two people overlapped) to make an unrelated query look like
    a strong match, and the more references you add the worse it gets — the
    exact opposite of the intended "more reference images = more reliable".
    Averaging the top-k requires the match to be consistent with several
    independent views of that person instead of just the single friendliest.
    """
    if bank is None or bank.size == 0:
        return -1.0
    sims = bank @ vec
    if sims.size <= k:
        return float(np.mean(sims))
    return float(np.mean(np.partition(sims, -k)[-k:]))


def top_two(scores: Sequence[Tuple[str, float]]) -> Tuple[str, float, float]:
    """Return (best_name, best_score, margin_over_runner_up).

    Margin is what turns nearest-neighbour lookup into open-set
    recognition: with a single absolute threshold, a garbage embedding is
    still *closest* to somebody and will be accepted the moment noise
    carries it over the line. Requiring the winner to beat the runner-up by
    a real gap rejects the "equally unlike everyone" case, which is what a
    crop of a stranger — or of an unrecognisable back-of-head — actually
    looks like.
    """
    if not scores:
        return "", -1.0, 0.0
    ordered = sorted(scores, key=lambda kv: -kv[1])
    best_name, best = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else -1.0
    return best_name, best, best - runner_up
