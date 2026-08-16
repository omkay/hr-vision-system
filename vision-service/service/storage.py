"""Source resolution — normalise any input (local path, URL, S3, RTSP) to a
local file path that the rest of the pipeline can open with cv2/imread.

Usage
-----
    with resolve_source(source) as local_path:
        cap = cv2.VideoCapture(local_path)

Remote files are downloaded to a temp file which is deleted on context exit.
Streams (RTSP, webcam index) are yielded as-is — cv2 handles them natively.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Generator


def _is_stream(source: str) -> bool:
    """True for sources cv2.VideoCapture can open directly without a local file."""
    return (
        source.startswith("rtsp://")
        or source.startswith("rtsps://")
        or source.startswith("rtmp://")
        or source.isdigit()          # webcam index
    )


def _suffix(source: str) -> str:
    """Best-guess file extension from a URL or path, defaulting to .mp4."""
    clean = source.split("?")[0].split("#")[0]
    return Path(clean).suffix or ".mp4"


@contextlib.contextmanager
def resolve_source(source: str) -> Generator[str, None, None]:
    """Yield a local file path for *source*, downloading remote files as needed.

    Supported schemes
    -----------------
    - Local path           → yielded as-is (existence checked by caller)
    - http:// / https://   → downloaded to a NamedTemporaryFile, deleted on exit
    - s3://bucket/key      → downloaded via boto3, deleted on exit
                             (boto3 must be installed; AWS credentials must be
                              available via the standard credential chain)
    - rtsp:// / rtmp:// / webcam index
                           → yielded as-is (cv2 opens these natively)
    """

    # ── RTSP / RTMP streams and webcam indices ─────────────────────────────────
    if _is_stream(source):
        yield source
        return

    # ── HTTP / HTTPS ───────────────────────────────────────────────────────────
    if source.startswith("http://") or source.startswith("https://"):
        tmp = tempfile.NamedTemporaryFile(suffix=_suffix(source), delete=False)
        tmp.close()
        try:
            urllib.request.urlretrieve(source, tmp.name)
            yield tmp.name
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        return

    # ── S3  s3://bucket/key ────────────────────────────────────────────────────
    if source.startswith("s3://"):
        try:
            import boto3  # type: ignore[import]
        except ImportError:
            raise RuntimeError(
                "boto3 is required for S3 sources. "
                "Install it with:  pip install boto3"
            )
        without_scheme = source[5:]
        bucket, _, key = without_scheme.partition("/")
        if not key:
            raise ValueError(f"Invalid S3 URI (missing key): {source}")

        tmp = tempfile.NamedTemporaryFile(suffix=_suffix(key), delete=False)
        tmp.close()
        try:
            boto3.client("s3").download_file(bucket, key, tmp.name)
            yield tmp.name
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        return

    # ── Local path ─────────────────────────────────────────────────────────────
    yield source


def is_remote(source: str) -> bool:
    """Return True if source requires a network fetch (not a local path or stream)."""
    return source.startswith(("http://", "https://", "s3://"))
