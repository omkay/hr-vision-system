"""Identity fusion — open-set face + ReID matching with per-frame assignment.

Design notes, since this replaces a simpler scheme that failed in two
specific, reproducible ways on real footage:

1. **Nearest neighbour is not recognition.** The old `match_reid` returned
   `argmax(similarity)` if it cleared one global threshold. Every crop
   therefore had a "best" employee, including crops of unenrolled visitors
   and crops carrying no usable information at all. Now a match must clear
   the threshold *and* beat the runner-up by a margin, on a crop that passed
   the quality gate (quality.py), scored against the mean of the top-k
   reference vectors rather than the single friendliest one.

2. **Per-track greedy decisions can't respect a global constraint.** Each
   track used to pick its own identity in isolation; the physical fact that
   one employee cannot be two people in one frame was then enforced after
   the fact by `revoke()`, which *blacklisted the name for the rest of the
   video*. One false positive thereby permanently deleted a correct
   identity — an employee re-entering later in the clip could never be
   recognised again no matter how clean the evidence. That mechanism is
   gone. `resolve_frame()` instead solves the whole frame at once as an
   assignment problem, so one-identity-per-frame holds by construction, the
   better-supported track keeps the contested name, and the other track
   simply resumes voting.

3. **Commitment was cheap and irreversible.** Five body-only frames locked a
   track's name forever. Commitment now requires face evidence and a higher
   vote score, and remains revisable (see SWITCH_MIN_MARGIN).

Every decision is optionally recorded (`debug=True`) with the scores that
produced it — the thresholds in config.py went through 0.75 → 0.60 → 0.65
by trial and error precisely because nothing was ever logged.
"""
from __future__ import annotations

import collections
import dataclasses
import logging
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .config import (
    COMMIT_MIN_RATIO, COMMIT_MIN_SCORE, COMMIT_MIN_SCORE_DAILY_BODY,
    COMMIT_REQUIRE_FACE,
    DEFAULT_FACE_MARGIN, DEFAULT_REID_MARGIN, FACE_ASSIGN_BONUS,
    FACE_VOTE_WEIGHT, PROVISIONAL_MIN_SCORE, REID_TOPK, REID_VOTE_WEIGHT,
    RELINK_MIN_APPEARANCE_SIM, STICKY_ASSIGN_SCORE, SWITCH_MIN_MARGIN,
    UNKNOWN_VOTE_WEIGHT,
)
from .quality import (
    CropQuality, assess_crop, normalize_illumination, top_two, topk_similarity,
)

log = logging.getLogger(__name__)

UNKNOWN_LABEL = "UNKNOWN"

_NO_MATCH = -1e6  # assignment-matrix entry for a (track, name) pair that isn't a candidate


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if a.size == 0 or b.size == 0:
        return np.zeros(
            (a.shape[0] if a.ndim == 2 else 0, b.shape[0] if b.ndim == 2 else 0),
            np.float32,
        )
    return a @ b.T


def _solve_assignment(matrix: np.ndarray) -> List[Tuple[int, int]]:
    """Maximum-weight one-to-one matching over *matrix* (rows × cols).

    Uses scipy's Hungarian solver when available; falls back to greedy
    highest-score-first, which is not guaranteed optimal but still enforces
    the one-identity-per-frame constraint (the property that actually
    matters here — optimality only changes which of two competing tracks
    keeps a contested name in rare near-tie cases).
    """
    if matrix.size == 0:
        return []
    try:
        from scipy.optimize import linear_sum_assignment
        rows, cols = linear_sum_assignment(matrix, maximize=True)
        return list(zip(rows.tolist(), cols.tolist()))
    except ImportError:  # pragma: no cover - scipy is a declared dependency
        log.warning("scipy unavailable — falling back to greedy identity assignment")
        pairs, used_r, used_c = [], set(), set()
        order = np.dstack(np.unravel_index(np.argsort(-matrix, axis=None), matrix.shape))[0]
        for r, c in order:
            if r in used_r or c in used_c:
                continue
            used_r.add(int(r)); used_c.add(int(c))
            pairs.append((int(r), int(c)))
        return pairs


def duplicate_alias_map(gallery) -> Dict[str, str]:
    """Map each enrolled name to a canonical name for its duplicate group.

    Why this exists: 5002 and 5004 were enrolled from the SAME photos — one
    person, two records, byte-identical embeddings. Every query then scored
    them equally, so the top-1/top-2 margin was exactly 0.000 and open-set
    rejection discarded a 0.970 face match. The system was refusing to answer
    "which of these two identical entries is it?", which is not a question
    about the person in front of the camera at all.

    Collapsing a duplicate group to one candidate makes the margin measure
    what it was meant to measure: how much better the best match is than the
    best DIFFERENT person. Duplicates are detected from the embeddings rather
    than from any naming convention, since nothing in the data marks them.

    Note this is a safety net, not a substitute for fixing the enrollment:
    the canonical name is chosen deterministically (lowest sorted name), so
    whichever record loses stops receiving attendance entirely.
    """
    names = list(gallery.names)
    parent = {n: n for n in names}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            # Keep the lexicographically smaller name as the group root so
            # the canonical choice never depends on iteration order.
            lo, hi = sorted((ra, rb))
            parent[hi] = lo

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            fa, fb = gallery.face_vecs[i], gallery.face_vecs[j]
            same_face = (np.linalg.norm(fa) > 1e-6 and np.linalg.norm(fb) > 1e-6
                         and float(fa @ fb) > 0.99)
            ba = gallery.reid_banks.get(names[i])
            bb = gallery.reid_banks.get(names[j])
            same_body = (ba is not None and bb is not None and ba.size and bb.size
                         and ba.shape == bb.shape and np.allclose(ba, bb))
            if same_face or same_body:
                union(names[i], names[j])

    alias = {n: find(n) for n in names}
    groups: Dict[str, list] = collections.defaultdict(list)
    for n, c in alias.items():
        groups[c].append(n)
    for c, members in groups.items():
        if len(members) > 1:
            log.warning(
                "gallery contains duplicate identities %s — treating them as one "
                "person (%r) for matching. Fix the enrollment: only %r will be "
                "credited with attendance.", sorted(members), c, c,
            )
    return alias


@dataclasses.dataclass
class Candidate:
    """One plausible identity for one crop, from one modality."""
    name: str
    score: float          # raw cosine similarity
    margin: float         # score minus the runner-up's score
    method: str           # "face" | "reid_daily" | "reid_enroll"

    @property
    def assign_score(self) -> float:
        """Score used in the assignment matrix — face outranks body always."""
        return self.score + (FACE_ASSIGN_BONUS if self.method == "face" else 0.0)


@dataclasses.dataclass
class Observation:
    """Everything one frame tells us about one track."""
    track_id: int
    quality: CropQuality
    candidates: List[Candidate] = dataclasses.field(default_factory=list)
    reid_vec: Optional[np.ndarray] = None
    # Diagnostics for the debug log: best raw score per modality regardless of
    # whether it passed the threshold/margin tests.
    best_face: Optional[Tuple[str, float, float]] = None   # (name, score, margin)
    best_reid: Optional[Tuple[str, float, float]] = None
    reid_source: str = ""   # "daily" | "enroll" — which bank was scored against

    @property
    def usable(self) -> bool:
        return self.quality.ok

    def candidate_for(self, name: str) -> Optional[Candidate]:
        for c in self.candidates:
            if c.name == name:
                return c
        return None


class IdentityFuser:
    def __init__(self, gallery, face_emb, reid_emb,
                 face_thr: float = 0.45, reid_thr: float = 0.75, window: int = 30,
                 daily_gallery=None,
                 face_margin: float = DEFAULT_FACE_MARGIN,
                 reid_margin: float = DEFAULT_REID_MARGIN,
                 debug: bool = False):
        self.gallery = gallery
        self.face_emb = face_emb
        self.reid_emb = reid_emb
        self.face_thr = face_thr
        self.reid_thr = reid_thr
        self.face_margin = face_margin
        self.reid_margin = reid_margin
        self.window = window
        # Optional DailyGallery (see daily_gallery.py) — today's fresh
        # ReID reference, generated from this same employee's checkin video
        # earlier today. Checked before the static enrollment reid_banks in
        # match_reid() since it reflects today's actual clothing/appearance.
        self.daily_gallery = daily_gallery
        # track_id -> deque of (name, weight, method) within the vote window.
        self.votes: Dict[int, collections.deque] = collections.defaultdict(
            lambda: collections.deque(maxlen=window)
        )
        self.committed: Dict[int, str] = {}
        # Last body embedding seen per track — used to verify an appearance
        # match before a new track inherits a vacated track's identity
        # (see can_relink); geometry alone mis-links people at doorways.
        self.last_reid_vec: Dict[int, np.ndarray] = {}
        self.debug = debug
        self.debug_rows: List[dict] = []
        # name -> canonical name, collapsing employees enrolled from the same
        # photos so they don't cancel each other out in the margin test.
        self.alias = duplicate_alias_map(gallery)

    # ── Scoring ──────────────────────────────────────────────────────────────

    def _collapse_duplicates(self, scores: List[Tuple[str, float]]) -> List[Tuple[str, float]]:
        """One entry per distinct PERSON, keeping each group's best score.

        Applied before every top-1/top-2 comparison so that duplicate
        enrollment records can't drive the margin to zero — see
        duplicate_alias_map().
        """
        best: Dict[str, float] = {}
        for name, s in scores:
            c = self.alias.get(name, name)
            if c not in best or s > best[c]:
                best[c] = s
        return list(best.items())

    def _face_scores(self, crop) -> Tuple[Optional[np.ndarray], List[Tuple[str, float]]]:
        v = self.face_emb.embed(crop)
        if v is None:
            return None, []
        sims = cosine_matrix(v[None, :], self.gallery.face_vecs)[0]
        return v, self._collapse_duplicates(list(zip(self.gallery.names, sims.tolist())))

    def _reid_scores(self, vec: np.ndarray) -> Tuple[List[Tuple[str, float]], str]:
        """Per-employee ReID scores, preferring today's fingerprints.

        Returns (scores, source) where source is "daily" or "enroll" — the
        daily bank (same clothes, same camera network as this footage) is
        used when it has any entry at all, since mixing scores from two
        banks with different reliability into one ranking would make the
        margin test meaningless.
        """
        banks, source = None, "enroll"
        if self.daily_gallery is not None and self.daily_gallery.reid_banks:
            banks, source = self.daily_gallery.reid_banks, "daily"
        else:
            banks = self.gallery.reid_banks
        scores = [(name, topk_similarity(bank, vec, REID_TOPK))
                  for name, bank in banks.items() if bank is not None and bank.size]
        return self._collapse_duplicates(scores), source

    def observe(self, track_id: int, crop, bbox: Sequence[int],
                frame_w: int, frame_h: int) -> Observation:
        """Gate, embed and score one person crop. No decisions taken here."""
        quality = assess_crop(crop, bbox, frame_w, frame_h)
        obs = Observation(track_id=track_id, quality=quality)
        if not quality.ok:
            return obs

        face_thr = self.face_thr + quality.penalty
        reid_thr = self.reid_thr + quality.penalty

        _, face_scores = self._face_scores(crop)
        if face_scores:
            name, score, margin = top_two(face_scores)
            obs.best_face = (name, score, margin)
            if score >= face_thr and margin >= self.face_margin:
                obs.candidates.append(Candidate(name, score, margin, "face"))

        norm_crop = normalize_illumination(crop)
        obs.reid_vec = self.reid_emb.embed(norm_crop)
        self.last_reid_vec[track_id] = obs.reid_vec
        reid_scores, source = self._reid_scores(obs.reid_vec)
        if reid_scores:
            name, score, margin = top_two(reid_scores)
            obs.best_reid = (name, score, margin)
            obs.reid_source = source
            already = obs.candidate_for(name) is not None
            if not already and score >= reid_thr and margin >= self.reid_margin:
                # The method records WHICH bank matched, because that decides
                # whether this evidence can ever commit a track: a daily
                # fingerprint traces back to a face match at check-in, the
                # static enrollment bank vouches for nothing.
                obs.candidates.append(Candidate(name, score, margin, f"reid_{source}"))

        obs.candidates.sort(key=lambda c: -c.assign_score)
        return obs

    # ── Per-frame resolution ─────────────────────────────────────────────────

    def resolve_frame(self, observations: Sequence[Observation],
                      frame_idx: int = -1) -> Dict[int, str]:
        """Assign at most one identity per track and one track per identity.

        Solving the frame as a whole is what makes the old blacklist
        unnecessary: the constraint "an employee appears at most once in a
        frame" is imposed *before* anything is voted on or drawn, instead of
        being detected afterwards and punished by discarding the name.
        """
        usable = [o for o in observations if o.usable and o.candidates]
        names = sorted({c.name for o in usable for c in o.candidates}
                       | {self.committed[o.track_id] for o in observations
                          if o.track_id in self.committed})

        assigned: Dict[int, Tuple[str, str, float]] = {}  # track -> (name, method, score)
        if names:
            rows = [o for o in observations if o.usable]
            # Committed tracks whose crop was rejected this frame still need a
            # row, otherwise their name is free for someone else to take.
            rows += [o for o in observations
                     if not o.usable and o.track_id in self.committed]
            name_idx = {n: i for i, n in enumerate(names)}
            matrix = np.full((len(rows), len(names)), _NO_MATCH, dtype=np.float64)
            for r, obs in enumerate(rows):
                for cand in obs.candidates:
                    matrix[r, name_idx[cand.name]] = cand.assign_score
                held = self.committed.get(obs.track_id)
                if held is not None:
                    c = obs.candidate_for(held)
                    # Stickiness: hold your own name against weak challengers,
                    # but a face-backed claim from another track outbids it.
                    matrix[r, name_idx[held]] = max(
                        matrix[r, name_idx[held]],
                        STICKY_ASSIGN_SCORE if c is None else c.assign_score,
                    )
            for r, c in _solve_assignment(matrix):
                if matrix[r, c] <= _NO_MATCH / 2:
                    continue
                obs, name = rows[r], names[c]
                cand = obs.candidate_for(name)
                method = cand.method if cand is not None else "sticky"
                assigned[obs.track_id] = (name, method, cand.score if cand else 0.0)

        labels: Dict[int, str] = {}
        for obs in observations:
            labels[obs.track_id] = self._apply(obs, assigned.get(obs.track_id), frame_idx)
        return labels

    def _apply(self, obs: Observation,
               award: Optional[Tuple[str, str, float]], frame_idx: int) -> str:
        """Fold this frame's assignment into *obs*'s track vote, return label."""
        tid = obs.track_id
        held = self.committed.get(tid)

        if not obs.usable:
            # No vote at all — a rejected crop is not evidence either way.
            label = held or self._provisional_label(tid)
            self._log(frame_idx, obs, award, label, "quality_reject")
            return label

        if award is None:
            self.votes[tid].append((UNKNOWN_LABEL, UNKNOWN_VOTE_WEIGHT, "none"))
        else:
            name, method, _ = award
            if method == "sticky":
                pass  # no fresh evidence, so no vote — just keeps the name
            else:
                weight = FACE_VOTE_WEIGHT if method == "face" else REID_VOTE_WEIGHT
                self.votes[tid].append((name, weight, method))

        if held is not None:
            # Committed, but revisable: if the assignment took the name away
            # from this track, or another name has out-voted it decisively
            # with face support, drop the commitment and go back to voting.
            lost_name = award is not None and award[0] != held
            if lost_name or self._should_uncommit(tid, held):
                del self.committed[tid]
                held = None
            else:
                self._log(frame_idx, obs, award, held, "committed")
                return held

        label = self._provisional_label(tid)
        self._maybe_commit(tid, label)
        self._log(frame_idx, obs, award, label,
                  "committed" if tid in self.committed else "provisional")
        return label

    # ── Vote bookkeeping ─────────────────────────────────────────────────────

    def _tally(self, track_id: int) -> collections.Counter:
        tally: collections.Counter = collections.Counter()
        for name, w, _ in self.votes.get(track_id, ()):
            tally[name] += w
        return tally

    def _has_face_vote(self, track_id: int, name: str) -> bool:
        return self._has_vote_method(track_id, name, "face")

    def _has_vote_method(self, track_id: int, name: str, method: str) -> bool:
        return any(n == name and m == method for n, _, m in self.votes.get(track_id, ()))

    def _provisional_label(self, track_id: int) -> str:
        tally = self._tally(track_id)
        if not tally:
            return UNKNOWN_LABEL
        winner, score = tally.most_common(1)[0]
        if winner == UNKNOWN_LABEL or score < PROVISIONAL_MIN_SCORE:
            return UNKNOWN_LABEL
        return winner

    def _maybe_commit(self, track_id: int, label: str) -> None:
        if label == UNKNOWN_LABEL or track_id in self.committed:
            return
        tally = self._tally(track_id)
        score = tally[label]
        total = sum(tally.values()) or 1.0
        if score < COMMIT_MIN_SCORE or score / total <= COMMIT_MIN_RATIO:
            return
        if not COMMIT_REQUIRE_FACE or self._has_face_vote(track_id, label):
            self.committed[track_id] = label
            return
        # No face here — but a match against today's fingerprint carries a
        # face confirmation from check-in with it, so it may commit at a
        # higher score. See COMMIT_MIN_SCORE_DAILY_BODY. A match against the
        # static enrollment bank has no such backing and stays provisional:
        # OSNet alone cannot separate two similarly-dressed colleagues
        # reliably enough to justify locking a name in.
        if self._has_vote_method(track_id, label, "reid_daily") \
                and score >= COMMIT_MIN_SCORE_DAILY_BODY:
            self.committed[track_id] = label

    def _should_uncommit(self, track_id: int, held: str) -> bool:
        tally = self._tally(track_id)
        for name, score in tally.items():
            if name in (held, UNKNOWN_LABEL):
                continue
            if score - tally[held] >= SWITCH_MIN_MARGIN and self._has_face_vote(track_id, name):
                return True
        return False

    # ── Track re-linking ─────────────────────────────────────────────────────

    def can_relink(self, new_obs: Observation, donor_track_id: int,
                   live_track_ids: Iterable[int]) -> bool:
        """Whether *new_obs*'s track may inherit donor_track_id's identity.

        Geometric proximity is decided by the caller (pipeline.py); this adds
        the two checks that keep the heuristic honest:

        * the new crop must actually *look like* the donor track's last known
          appearance — at a doorway or corner, "a new box appeared where a
          tracked person just vanished" describes the next person through
          the door just as well as the same person continuing on;
        * the donor's identity must not already be visible on another track
          in this frame, which would clone one employee into two.
        """
        name = self.committed.get(donor_track_id)
        if name is None or new_obs.reid_vec is None:
            return False
        for tid in live_track_ids:
            if tid != new_obs.track_id and self.committed.get(tid) == name:
                return False
        donor_vec = self.last_reid_vec.get(donor_track_id)
        if donor_vec is None:
            return False
        sim = float(np.dot(donor_vec, new_obs.reid_vec))
        return sim >= RELINK_MIN_APPEARANCE_SIM

    def adopt(self, track_id: int, name: str) -> None:
        """Force-commit *track_id* to *name*, bypassing vote accumulation.

        Used only for verified track re-linking (see can_relink): ByteTrack
        loses a track and starts a new one moments later for the same
        physical person, most often because their visible appearance changed
        mid-clip (removing a jacket) — exactly the case same-day ReID is
        weakest against, where the new track might otherwise never re-earn
        its identity. Still revisable afterwards: a wrong adoption is undone
        by the same uncommit paths as a wrong commit.
        """
        self.committed[track_id] = name

    # ── Single-image helpers (used by /checkin) ───────────────────────────────

    def match_face(self, crop) -> Optional[Tuple[str, float]]:
        _, scores = self._face_scores(crop)
        if not scores:
            return None
        name, score, margin = top_two(scores)
        if score >= self.face_thr and margin >= self.face_margin:
            return name, score
        return None

    def match_reid(self, crop) -> Optional[Tuple[str, float]]:
        vec = self.reid_emb.embed(normalize_illumination(crop))
        scores, _ = self._reid_scores(vec)
        if not scores:
            return None
        name, score, margin = top_two(scores)
        if score >= self.reid_thr and margin >= self.reid_margin:
            return name, score
        return None

    def update(self, track_id: int, crop, bbox=None,
               frame_w: int = 0, frame_h: int = 0) -> str:
        """Single-track convenience wrapper around observe() + resolve_frame().

        Kept for callers that genuinely handle one person at a time. The
        per-frame uniqueness constraint cannot apply to a single observation,
        so multi-person video paths must use observe()/resolve_frame().
        """
        h, w = crop.shape[:2]
        bbox = bbox if bbox is not None else (1, 1, w - 1, h - 1)
        obs = self.observe(track_id, crop, bbox, frame_w or w + 2, frame_h or h + 2)
        return self.resolve_frame([obs])[track_id]

    # ── Debug log ────────────────────────────────────────────────────────────

    def _log(self, frame_idx: int, obs: Observation,
             award: Optional[Tuple[str, str, float]], label: str, state: str) -> None:
        if not self.debug:
            return
        q = obs.quality
        fname, fscore, fmargin = obs.best_face or ("", 0.0, 0.0)
        rname, rscore, rmargin = obs.best_reid or ("", 0.0, 0.0)
        tally = self._tally(obs.track_id)
        self.debug_rows.append({
            "frame": frame_idx,
            "track_id": obs.track_id,
            "label": label,
            "state": state,
            "assigned_name": award[0] if award else "",
            "assigned_method": award[1] if award else "",
            "quality_ok": q.ok,
            "quality_flags": q.reason_str,
            "quality_penalty": round(q.penalty, 3),
            "crop_h": q.height,
            "crop_aspect": round(q.aspect, 2),
            "crop_blur": round(q.blur, 1),
            "face_top1": fname,
            "face_score": round(fscore, 4),
            "face_margin": round(fmargin, 4),
            "reid_top1": rname,
            "reid_score": round(rscore, 4),
            "reid_margin": round(rmargin, 4),
            # "daily" = matched against today's check-in fingerprints,
            # "enroll" = fell back to the static bank (which is EMPTY when
            # employees are enrolled with face photos only — so "enroll"
            # here means body matching had nothing to compare against at
            # all, and that is the thing to fix, not the threshold).
            "reid_source": obs.reid_source,
            "vote_score": round(tally.get(label, 0.0), 2),
            "vote_total": round(sum(tally.values()), 2),
        })
