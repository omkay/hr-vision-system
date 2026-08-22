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
import http.client
import logging
import os
import shutil
import socket
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Generator

log = logging.getLogger(__name__)


class _TruncatedDownload(Exception):
    """Raised when a response's socket closes cleanly (no underlying
    exception at all) before as many bytes as Content-Length promised were
    actually read. http.client.IncompleteRead exists for this same purpose
    but expects the *actual partial bytes* as its first argument (its
    __repr__ calls len() on it) — we've already streamed those bytes to disk
    rather than held them in memory, so a plain exception carrying just the
    counts is simpler and avoids passing the wrong type into a stdlib
    exception that expects something else.
    """
    def __init__(self, got: int, expected: int):
        super().__init__(f"got only {got} of {expected} expected bytes")
        self.got = got
        self.expected = expected


_DOWNLOAD_RETRIES = 5
_CHUNK_SIZE = 1024 * 1024  # 1 MiB
_RETRYABLE_ERRORS = (
    urllib.error.URLError,
    http.client.IncompleteRead,
    http.client.HTTPException,
    _TruncatedDownload,
    ConnectionError,
    socket.timeout,
    TimeoutError,
    OSError,
)


def _download_with_resume(url: str, dest: str) -> None:
    """Download *url* to *dest*, resuming (via Range) after a dropped
    connection instead of failing outright on the first hiccup.

    Replaces a bare urllib.request.urlretrieve() call, which has no retry or
    resume logic at all — over a remote link (e.g. Tailscale, rather than a
    same-host Docker network) a large video (tens of MB) served by a
    single-threaded dev server (`php artisan serve` / `php -S`, not a real
    production HTTP server — see the note on the caller below) can easily
    have its connection reset partway through, surfacing as
    ContentTooShortError ("got only X out of Y bytes"). Each retry resumes
    from the last byte actually written (if the server honors Range —
    verified by checking for a 206 response) rather than restarting the
    whole transfer, since redownloading an 80MB+ file from scratch on every
    dropped packet would make an already-flaky link far worse.
    """
    written = 0
    last_err: Exception | None = None
    for attempt in range(1, _DOWNLOAD_RETRIES + 1):
        req = urllib.request.Request(url)
        if written > 0:
            req.add_header("Range", f"bytes={written}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resumed = written > 0 and getattr(resp, "status", None) == 206
                mode = "ab" if resumed else "wb"
                if written > 0 and not resumed:
                    # Server ignored Range and is sending the whole file
                    # again from byte 0 — restart the local file too, or
                    # we'd end up with the old partial bytes followed by a
                    # full second copy.
                    written = 0
                # Total size we expect once this response completes: for a
                # fresh (non-resumed) request that's just this response's
                # own Content-Length; for a resumed 206 it's what's already
                # on disk plus the remaining bytes this response carries.
                content_length = resp.getheader("Content-Length")
                expected_total = None
                if content_length is not None:
                    expected_total = written + int(content_length)
                with open(dest, mode) as f:
                    while True:
                        chunk = resp.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        f.write(chunk)
                        written += len(chunk)
                # A single-threaded dev server dropping the connection often
                # closes the socket cleanly rather than raising — read()
                # just returns b"" as if it were a normal EOF. Without this
                # check that silently produces a truncated file that LOOKS
                # like a successful download (no exception at all), which is
                # worse than the original ContentTooShortError this
                # function exists to fix in the first place.
                if expected_total is not None and written < expected_total:
                    raise _TruncatedDownload(written, expected_total)
            return  # completed without the loop breaking early
        except _RETRYABLE_ERRORS as e:
            last_err = e
            log.warning(
                "download attempt %d/%d for %s failed after %d bytes (%s) — retrying",
                attempt, _DOWNLOAD_RETRIES, url, written, e,
            )
            time.sleep(min(2 ** attempt, 15))
    raise RuntimeError(
        f"failed to download {url} after {_DOWNLOAD_RETRIES} attempts "
        f"({written} bytes written so far): {last_err}"
    ) from last_err



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
        # Tried opening these URLs directly via cv2/ffmpeg to skip the local
        # download entirely (avoids staging a second full copy of every
        # video). Reverted: cv2.VideoCapture(url).isOpened() returned True
        # even when the backing server (PHP's built-in dev server, not a real
        # production HTTP server) couldn't sustain the full sequential stream
        # — confirmed via a real test that came back with frames_read: 0
        # despite "opening" successfully. Full download is slower but actually
        # correct against this stack; revisit if backend-service ever moves
        # off `php -S`.
        #
        # _download_with_resume() (not plain urlretrieve) specifically
        # because that same dev-server fragility shows up here too, just as
        # a dropped connection partway through rather than a silent
        # zero-frame read — worse the more remote/lossy the link (e.g.
        # Tailscale rather than a same-host Docker network), since a
        # single-threaded dev server has no real resilience to hold a
        # multi-second transfer open under any concurrent load.
        tmp = tempfile.NamedTemporaryFile(suffix=_suffix(source), delete=False)
        tmp.close()
        try:
            _download_with_resume(source, tmp.name)
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
