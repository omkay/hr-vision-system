"""Daily body-fingerprint store — per-employee, per-day ReID embeddings.

Static enrollment (gallery.py) captures a face embedding once (stable over
time) plus a body/ReID bank built from whatever photos were provided at
enrollment (NOT stable — clothes, lighting, and build change day to day).
Zone cameras frequently can't see a face clearly (angle, distance,
resolution), so cross-camera tracking there leans on ReID body matching —
and matching against a stale enrollment-day outfit hurts precision.

This module stores a *fresh* per-day ReID reference instead. The moment an
employee is face-identified in that day's checkin video, the pipeline grabs
body crops from that same clip and saves them here, keyed by
(date, employee_id). Zone videos processed for that date then match against
today's actual appearance first (see IdentityFuser.match_reid), falling back
to the static enrollment bank when there's no entry for today (e.g. the
employee didn't check in) or nothing clears the ReID threshold.

Persisted as one .npz file per date under gallery/daily/<date>.npz — kept
separate from gallery.npz (the permanent enrollment file) so a day's
transient fingerprints can never corrupt or get mixed into the permanent
identity data. Old dates are never actively pruned by this module; this
repo runs as a demo/thesis project rather than a long-lived production
instance, so rotation is left as an operational concern if that changes.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Dict

import numpy as np

from .config import GALLERY_DIR

DAILY_GALLERY_DIR = GALLERY_DIR / "daily"
DAILY_GALLERY_DIR.mkdir(parents=True, exist_ok=True)


def _path_for(date: str) -> Path:
    return DAILY_GALLERY_DIR / f"{date}.npz"


@dataclasses.dataclass
class DailyGallery:
    """Same shape as EmployeeGallery.reid_banks (name -> stacked ReID
    vectors), scoped to a single calendar day."""

    date: str
    reid_banks: Dict[str, np.ndarray]

    def save(self) -> None:
        if not self.reid_banks:
            return
        np.savez(
            _path_for(self.date),
            reid_keys=np.array(list(self.reid_banks.keys())),
            **{f"reid_{k}": v for k, v in self.reid_banks.items()},
        )

    @staticmethod
    def load(date: str) -> "DailyGallery":
        p = _path_for(date)
        if not p.exists():
            return DailyGallery(date=date, reid_banks={})
        z = np.load(p, allow_pickle=True)
        return DailyGallery(
            date=date,
            reid_banks={k: z[f"reid_{k}"] for k in z["reid_keys"]},
        )

    def exists(self) -> bool:
        return _path_for(self.date).exists()


def save_fingerprint(date: str, employee_id: str, reid_vecs: np.ndarray) -> None:
    """Merge/overwrite one employee's ReID bank for *date* and persist.

    reid_vecs: (N, 512) array of L2-normalised ReID embeddings collected
    from that employee's own checkin-video appearance today. This
    *overwrites* any previous entry for this employee/date (e.g. a re-run
    checkin) rather than accumulating across calls, so a bad crop from an
    earlier attempt can't linger in the bank forever.
    """
    if reid_vecs is None or reid_vecs.size == 0:
        return
    dg = DailyGallery.load(date)
    dg.reid_banks[employee_id] = reid_vecs
    dg.save()


def load_daily_gallery(date: str) -> DailyGallery:
    return DailyGallery.load(date)
