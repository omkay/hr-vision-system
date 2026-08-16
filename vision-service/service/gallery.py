"""Employee gallery: per-person face + body embeddings, persisted as .npz."""
from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path
from typing import Dict, List

import cv2
import numpy as np

from .config import GALLERY_DIR, GALLERY_PATH
from .models import FaceEmbedder, ReIDEmbedder
from .storage import resolve_source


@dataclasses.dataclass
class EmployeeGallery:
    names: List[str]
    face_vecs: np.ndarray
    reid_banks: Dict[str, np.ndarray]

    def save(self, path: Path):
        np.savez(path, names=np.array(self.names), face=self.face_vecs,
                 reid_keys=np.array(list(self.reid_banks.keys())),
                 **{f"reid_{k}": v for k, v in self.reid_banks.items()})

    @staticmethod
    def load(path: Path) -> "EmployeeGallery":
        z = np.load(path, allow_pickle=True)
        return EmployeeGallery(
            names=z["names"].tolist(),
            face_vecs=z["face"],
            reid_banks={k: z[f"reid_{k}"] for k in z["reid_keys"]},
        )


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
    bank = reid_emb.embed_batch(crops) if crops else np.zeros((0, 512), np.float32)
    return face_vec, bank, len(fvs), len(crops)


def build_gallery(face_emb: FaceEmbedder, reid_emb: ReIDEmbedder) -> EmployeeGallery:
    names, face_vecs, reid_banks = [], [], {}
    people = [p for p in sorted(GALLERY_DIR.iterdir()) if p.is_dir()]
    if not people:
        raise RuntimeError(f"No sub-folders under {GALLERY_DIR}.")
    for person in people:
        face_vec, bank, _, _ = _embed_person(person, face_emb, reid_emb)
        names.append(person.name)
        face_vecs.append(face_vec)
        reid_banks[person.name] = bank
    return EmployeeGallery(
        names=names,
        face_vecs=np.stack(face_vecs),
        reid_banks=reid_banks,
    )


def load_or_build(face_emb: FaceEmbedder, reid_emb: ReIDEmbedder,
                  rebuild: bool = False) -> EmployeeGallery:
    if not rebuild and GALLERY_PATH.exists():
        return EmployeeGallery.load(GALLERY_PATH)
    g = build_gallery(face_emb, reid_emb)
    g.save(GALLERY_PATH)
    return g


def enroll_person(name: str, face_paths: List[str], body_paths: List[str],
                  face_emb: FaceEmbedder, reid_emb: ReIDEmbedder) -> dict:
    """Copy provided images into gallery/<name>/{face,body}/, then rebuild the
    gallery file. Returns a summary dict."""
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"Invalid employee name: {name!r}")
    person = _person_dir(name)
    copied_face, copied_body = 0, 0

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

    for src in face_paths:
        with resolve_source(src) as local_path:
            sp = Path(local_path)
            if not sp.exists():
                raise FileNotFoundError(f"Face image not found: {src}")
            shutil.copy2(sp, person / "face" / _dest_name(src, local_path))
        copied_face += 1
    for src in body_paths:
        with resolve_source(src) as local_path:
            sp = Path(local_path)
            if not sp.exists():
                raise FileNotFoundError(f"Body image not found: {src}")
            shutil.copy2(sp, person / "body" / _dest_name(src, local_path))
        copied_body += 1

    face_vec, bank, n_face, n_body = _embed_person(person, face_emb, reid_emb)

    # Merge into the existing gallery (or create a new one).
    if GALLERY_PATH.exists():
        gallery = EmployeeGallery.load(GALLERY_PATH)
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
        "total_employees": len(gallery.names),
    }
