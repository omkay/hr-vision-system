"""Identity fusion — temporal voting across face + ReID matches."""
from __future__ import annotations

import collections
from typing import Dict, Optional, Tuple

import numpy as np

UNKNOWN_LABEL = "UNKNOWN"


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if a.size == 0 or b.size == 0:
        return np.zeros(
            (a.shape[0] if a.ndim == 2 else 0, b.shape[0] if b.ndim == 2 else 0),
            np.float32,
        )
    return a @ b.T


class IdentityFuser:
    def __init__(self, gallery, face_emb, reid_emb,
                 face_thr: float = 0.45, reid_thr: float = 0.75, window: int = 30):
        self.gallery = gallery
        self.face_emb = face_emb
        self.reid_emb = reid_emb
        self.face_thr = face_thr
        self.reid_thr = reid_thr
        self.window = window
        self.votes: Dict[int, collections.deque] = collections.defaultdict(
            lambda: collections.deque(maxlen=window)
        )
        self.committed: Dict[int, str] = {}

    def match_face(self, crop) -> Optional[Tuple[str, float]]:
        v = self.face_emb.embed(crop)
        if v is None:
            return None
        sims = cosine_matrix(v[None, :], self.gallery.face_vecs)[0]
        i = int(np.argmax(sims)); s = float(sims[i])
        return (self.gallery.names[i], s) if s >= self.face_thr else None

    def match_reid(self, crop) -> Optional[Tuple[str, float]]:
        v = self.reid_emb.embed(crop)
        best_name, best_s = None, -1.0
        for name, bank in self.gallery.reid_banks.items():
            if bank.size == 0:
                continue
            s = float(np.max(bank @ v))
            if s > best_s:
                best_name, best_s = name, s
        if best_name is not None and best_s >= self.reid_thr:
            return best_name, best_s
        return None

    def update(self, track_id: int, crop) -> str:
        if track_id in self.committed:
            return self.committed[track_id]
        guess = self.match_face(crop)
        if guess is not None:
            self.votes[track_id].append((guess[0], 2.0))
        else:
            guess = self.match_reid(crop)
            if guess is not None:
                self.votes[track_id].append((guess[0], 1.0))
            else:
                self.votes[track_id].append((UNKNOWN_LABEL, 0.5))
        tally = collections.Counter()
        for name, w in self.votes[track_id]:
            tally[name] += w
        winner, score = tally.most_common(1)[0]
        total = sum(tally.values())
        if winner != UNKNOWN_LABEL and score / total > 0.55 and score >= 5:
            self.committed[track_id] = winner
        return winner
