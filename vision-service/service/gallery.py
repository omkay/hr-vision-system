"""Employee gallery: per-person face + body embeddings, persisted as .npz."""
from __future__ import annotations

import dataclasses
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

from .config import GALLERY_DIR, GALLERY_PATH, REID_PREPROC_VERSION
from . import daily_gallery
from .daily_gallery import DAILY_GALLERY_DIR
from .models import FaceEmbedder, ReIDEmbedder, get_face_embedder, get_reid_embedder
from .quality import normalize_illumination
from .storage import resolve_source

log = logging.getLogger(__name__)


@dataclasses.dataclass
class EmployeeGallery:
    names: List[str]
    face_vecs: np.ndarray
    reid_banks: Dict[str, np.ndarray]
    # Which ReID preprocessing the stored banks were built with (see
    # config.REID_PREPROC_VERSION). Files written before versioning existed
    # have no such key and are treated as version 1.
    preproc_version: int = REID_PREPROC_VERSION

    def save(self, path: Path):
        np.savez(path, names=np.array(self.names), face=self.face_vecs,
                 reid_keys=np.array(list(self.reid_banks.keys())),
                 preproc_version=np.array([self.preproc_version]),
                 **{f"reid_{k}": v for k, v in self.reid_banks.items()})

    @staticmethod
    def load(path: Path) -> "EmployeeGallery":
        z = np.load(path, allow_pickle=True)
        version = int(z["preproc_version"][0]) if "preproc_version" in z else 1
        return EmployeeGallery(
            names=z["names"].tolist(),
            face_vecs=z["face"],
            reid_banks={k: z[f"reid_{k}"] for k in z["reid_keys"]},
            preproc_version=version,
        )

    @property
    def is_stale(self) -> bool:
        """True when the stored banks predate the current preprocessing.

        Comparing a bank embedded from raw crops against queries embedded
        from illumination-normalised crops costs real cosine similarity and
        produces no error — just a system that mysteriously answers UNKNOWN
        more often than it used to.
        """
        return self.preproc_version != REID_PREPROC_VERSION


def _person_dir(name: str) -> Path:
    d = GALLERY_DIR / name
    (d / "face").mkdir(parents=True, exist_ok=True)
    (d / "body").mkdir(parents=True, exist_ok=True)
    return d


def _embed_person(person_dir: Path, face_emb: FaceEmbedder, reid_emb: ReIDEmbedder):
    face_dir, body_dir = person_dir / "face", person_dir / "body"
    fvs = []
    if face_dir.exists():
        for p in sorted(face_dir.glob("*.*")):
            img = cv2.imread(str(p))
            if img is None:
                continue
            v = face_emb.embed(img)
            if v is not None:
                fvs.append(v)
    if fvs:
        face_vec = np.mean(np.stack(fvs), axis=0)
        face_vec /= (np.linalg.norm(face_vec) + 1e-9)
    else:
        face_vec = np.zeros(512, np.float32)

    crops = []
    if body_dir.exists():
        for p in sorted(body_dir.glob("*.*")):
            c = cv2.imread(str(p))
            if c is not None:
                crops.append(c)
    # Enrollment body crops go through the same illumination normalisation as
    # query crops at inference time (see quality.normalize_illumination and
    # IdentityFuser.observe) — enrollment photos are typically taken in very
    # different light from NVR footage, and comparing a raw-lit bank against
    # normalised queries throws away similarity for no reason.
    crops = [normalize_illumination(c) for c in crops]
    bank = reid_emb.embed_batch(crops) if crops else np.zeros((0, 512), np.float32)
    return face_vec, bank, len(fvs), len(crops)


def _is_person_dir(p: Path) -> bool:
    """Whether *p* is an employee folder rather than service data.

    `gallery/` holds one directory per employee, but ALSO `gallery/daily/`,
    where daily_gallery.py caches per-day fingerprints. Scanning every
    subdirectory blindly enrolled that cache as an employee named "daily"
    (all-zero face vector, empty ReID bank) — harmless-looking, but it sat in
    the gallery's `names` list and in every similarity ranking. A person
    folder is identified positively, by having face/ or body/ inside it,
    rather than by trying to enumerate what to exclude.
    """
    if not p.is_dir() or p.name.startswith("."):
        return False
    if p.resolve() == DAILY_GALLERY_DIR.resolve():
        return False
    return (p / "face").is_dir() or (p / "body").is_dir()


def _warn_on_suspect_gallery(names, face_vecs, reid_banks) -> None:
    """Log enrollment problems that are invisible from the UI.

    Both of these were found in real data and each silently destroys
    identification for the employees involved, with no error anywhere:

    * Two employees enrolled from the SAME photos (5002 and 5004 were
      byte-identical). Their similarity scores are then equal for any query,
      so open-set matching correctly refuses to choose and both become
      permanently unidentifiable — while the older argmax behaviour picked
      one arbitrarily and mislabelled roughly half the time, which is worse.
    * An employee whose enrollment photos yielded NO face embedding at all
      (`55`, one photo, no face detected). Their face vector is all zeros, so
      they can never be face-matched, and on this system face is the only
      signal allowed to commit an identity at check-in.
    """
    for i, name in enumerate(names):
        if float(np.linalg.norm(face_vecs[i])) < 1e-6:
            log.warning(
                "employee %r has NO usable face embedding — no face was detected in "
                "their enrollment photo(s). They can never be face-identified; "
                "re-enroll with a clear, front-facing photo.", name,
            )
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            fa, fb = face_vecs[i], face_vecs[j]
            if np.linalg.norm(fa) < 1e-6 or np.linalg.norm(fb) < 1e-6:
                continue
            if float(fa @ fb) > 0.99:
                log.warning(
                    "employees %r and %r have near-identical FACE embeddings "
                    "(cos=%.4f) — almost certainly the same person enrolled twice, "
                    "or the same photo uploaded for both. Neither can be told apart "
                    "from the other; fix the enrollment data.",
                    names[i], names[j], float(fa @ fb),
                )
    keys = list(reid_banks)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = reid_banks[keys[i]], reid_banks[keys[j]]
            if a.size and b.size and a.shape == b.shape and np.allclose(a, b):
                log.warning(
                    "employees %r and %r have IDENTICAL body (ReID) banks — same "
                    "images enrolled for both. Body matching cannot distinguish "
                    "them at all.", keys[i], keys[j],
                )


def build_gallery(face_emb: FaceEmbedder, reid_emb: ReIDEmbedder) -> EmployeeGallery:
    names, face_vecs, reid_banks = [], [], {}
    people = [p for p in sorted(GALLERY_DIR.iterdir()) if _is_person_dir(p)]
    if not people:
        raise RuntimeError(f"No employee sub-folders under {GALLERY_DIR}.")
    for person in people:
        face_vec, bank, _, _ = _embed_person(person, face_emb, reid_emb)
        names.append(person.name)
        face_vecs.append(face_vec)
        reid_banks[person.name] = bank
    _warn_on_suspect_gallery(names, face_vecs, reid_banks)
    return EmployeeGallery(
        names=names,
        face_vecs=np.stack(face_vecs),
        reid_banks=reid_banks,
    )


def load_or_build(face_emb: FaceEmbedder, reid_emb: ReIDEmbedder,
                  rebuild: bool = False) -> EmployeeGallery:
    if not rebuild and GALLERY_PATH.exists():
        g = EmployeeGallery.load(GALLERY_PATH)
        if not g.is_stale:
            return g
        log.warning(
            "gallery.npz was built with ReID preprocessing v%d (current: v%d) — "
            "rebuilding from the images in %s",
            g.preproc_version, REID_PREPROC_VERSION, GALLERY_DIR,
        )
    g = build_gallery(face_emb, reid_emb)
    g.save(GALLERY_PATH)
    return g


def get_gallery() -> Optional[EmployeeGallery]:
    """The gallery every request path should use.

    Loads gallery.npz, and transparently re-embeds it when the stored banks
    were produced by older preprocessing than the current code — the source
    images are still on disk under gallery/<name>/, so this is recoverable
    without operator action. Returns None when nobody is enrolled yet.

    Routers must go through this rather than calling EmployeeGallery.load()
    directly, otherwise a stale file is used silently and identification
    quietly degrades across the board.
    """
    if not GALLERY_PATH.exists():
        # No .npz, but the enrollment images may still be there — e.g. after
        # deleting gallery.npz deliberately to force a clean rebuild. Build
        # from those rather than reporting "nobody is enrolled", which would
        # silently run the whole pipeline with identification disabled.
        if any(_is_person_dir(p) for p in GALLERY_DIR.iterdir()):
            log.info("gallery.npz missing — rebuilding from images in %s", GALLERY_DIR)
            return load_or_build(get_face_embedder(), get_reid_embedder(), rebuild=True)
        return None
    g = EmployeeGallery.load(GALLERY_PATH)
    if not g.is_stale:
        return g
    return load_or_build(get_face_embedder(), get_reid_embedder(), rebuild=True)


def delete_person(name: str) -> dict:
    """Remove an employee from the gallery completely.

    Three separate stores have to be cleaned or the identity comes back:

    1. `gallery/<name>/` — the enrollment images. Left behind, any rebuild
       (see get_gallery) re-enrolls the deleted employee from disk.
    2. `gallery.npz` — the embedded banks actually used for matching.
    3. `gallery/daily/*.npz` — today's and every past day's body
       fingerprints. These are the ones that matter most in practice: since
       match_reid prefers the daily bank over enrollment, a deleted
       employee's fingerprint would keep winning matches and attributing
       activity to an ID that no longer resolves to a person.

    Idempotent — deleting someone who isn't enrolled is not an error, since
    the upstream employee record may never have had photos uploaded.
    """
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"Invalid employee name: {name!r}")

    removed_images = False
    person = GALLERY_DIR / name
    if person.is_dir():
        shutil.rmtree(person)
        removed_images = True

    removed_from_gallery = False
    if GALLERY_PATH.exists():
        g = EmployeeGallery.load(GALLERY_PATH)
        if name in g.names:
            i = g.names.index(name)
            g.names.pop(i)
            g.face_vecs = np.delete(g.face_vecs, i, axis=0)
            g.reid_banks.pop(name, None)
            removed_from_gallery = True
            if g.names:
                g.save(GALLERY_PATH)
            else:
                # An .npz with zero employees can't be loaded back
                # meaningfully (np.stack of nothing) — drop the file instead
                # and let it be rebuilt if anyone is enrolled again.
                GALLERY_PATH.unlink(missing_ok=True)

    dates = daily_gallery.remove_employee(name)

    log.info("deleted employee %r from gallery (images=%s, gallery.npz=%s, daily=%s)",
             name, removed_images, removed_from_gallery, dates)
    return {
        "name": name,
        "images_removed": removed_images,
        "gallery_entry_removed": removed_from_gallery,
        "daily_fingerprint_dates_cleared": dates,
    }


def enroll_person(name: str, face_paths: List[str], body_paths: List[str],
                  face_emb: FaceEmbedder, reid_emb: ReIDEmbedder) -> dict:
    """Copy provided images into gallery/<name>/{face,body}/, then rebuild the
    gallery file. Returns a summary dict."""
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"Invalid employee name: {name!r}")
    person = _person_dir(name)

    # Full replace, not additive: callers (e.g. Hr_SmartPay) always send the
    # complete current set of photos for this person on every /enroll call,
    # including after a photo was deleted. Without clearing first, a deleted
    # photo's file would stay on disk forever and keep contributing to the
    # averaged face embedding / ReID bank.
    for existing in (person / "face").glob("*.*"):
        existing.unlink()
    for existing in (person / "body").glob("*.*"):
        existing.unlink()

    def _dest_name(src: str, local_path: str) -> str:
        # For remote sources, resolve_source() yields a random temp-file name —
        # keep the caller's original filename (minus query string) instead.
        clean = src.split("?")[0].split("#")[0]
        return Path(clean).name or Path(local_path).name

    # Unreachable sources are SKIPPED, not fatal. Callers send a whole set of
    # photos at once (see the full-replace note above), and an upstream
    # database row can easily outlive its file — when that happened, one dead
    # URL out of five made the entire enrollment fail with a 500, leaving the
    # employee unenrolled even though four good photos were available. The
    # skipped sources come back in the response so the caller can still see
    # something was wrong; only a total failure raises.
    skipped: List[str] = []

    def _copy_each(sources: List[str], subdir: str) -> int:
        copied = 0
        for src in sources:
            try:
                with resolve_source(src) as local_path:
                    sp = Path(local_path)
                    if not sp.exists():
                        raise FileNotFoundError(f"resolved path missing: {local_path}")
                    shutil.copy2(sp, person / subdir / _dest_name(src, local_path))
                copied += 1
            except Exception as e:
                log.warning("skipping unreachable %s image for %r: %s — %s",
                            subdir, name, src, e)
                skipped.append(src)
        return copied

    copied_face = _copy_each(face_paths, "face")
    copied_body = _copy_each(body_paths, "body")

    if skipped and copied_face == 0 and copied_body == 0:
        raise FileNotFoundError(
            f"None of the {len(skipped)} image(s) for {name!r} could be read: "
            f"{skipped[0]} (and {len(skipped) - 1} more)" if len(skipped) > 1
            else f"Could not read the only image for {name!r}: {skipped[0]}"
        )

    face_vec, bank, n_face, n_body = _embed_person(person, face_emb, reid_emb)

    # Merge into the existing gallery (or create a new one).
    if GALLERY_PATH.exists():
        gallery = EmployeeGallery.load(GALLERY_PATH)
        if gallery.is_stale:
            # Don't merge a freshly-preprocessed bank into stale ones — that
            # would leave one file holding two incompatible embedding spaces,
            # where some employees match well and others silently don't.
            log.warning("existing gallery is stale (v%d) — re-embedding everyone",
                        gallery.preproc_version)
            gallery = build_gallery(face_emb, reid_emb)
        if name in gallery.names:
            i = gallery.names.index(name)
            gallery.face_vecs[i] = face_vec
        else:
            gallery.names.append(name)
            gallery.face_vecs = np.vstack([gallery.face_vecs, face_vec[None, :]])
        gallery.reid_banks[name] = bank
    else:
        gallery = EmployeeGallery(
            names=[name],
            face_vecs=face_vec[None, :],
            reid_banks={name: bank},
        )
    gallery.save(GALLERY_PATH)

    return {
        "name": name,
        "face_images_copied": copied_face,
        "body_images_copied": copied_body,
        "face_embeddings_used": n_face,
        "body_embeddings_used": n_body,
        # Sources that could not be fetched. Compare face_embeddings_used
        # against face_images_copied too: a copied image whose face wasn't
        # detectable contributes nothing, and that is worth knowing at
        # enrollment time rather than discovering it as a missed checkin.
        "skipped_sources": skipped,
        "total_employees": len(gallery.names),
    }
