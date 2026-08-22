"""Paths and default thresholds shared across the service."""
from __future__ import annotations

import os
from pathlib import Path

try:
    import torch
    if torch.cuda.is_available():
        DEVICE = "cuda"
    elif torch.backends.mps.is_available():
        # Apple Silicon GPU, via PyTorch's Metal Performance Shaders backend.
        # Only reachable when this process runs natively on the Mac — Docker
        # Desktop's Linux VM has no path to the host's Metal device, so the
        # containerized deployment always falls through to "cpu" here even
        # on an M-series Mac. See models.py for where this actually gets
        # used (ReIDEmbedder / PersonObjectDetector via torch/ultralytics)
        # and _onnx_providers_for() for the separate onnxruntime/CoreML path
        # InsightFace's FaceEmbedder uses instead (torch device strings
        # don't apply to onnxruntime sessions).
        DEVICE = "mps"
    else:
        DEVICE = "cpu"
except ImportError:
    DEVICE = "cpu"


def _resolve_project_dir() -> Path:
    env = os.environ.get("PROJECT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve().parent.parent
    if (here / "employee_activity_tracking_marimo.py").exists():
        return here
    return Path.cwd().resolve()


PROJECT_DIR = _resolve_project_dir()
DATA_DIR    = PROJECT_DIR / "data"
OUT_DIR     = PROJECT_DIR / "outputs"
GALLERY_DIR = PROJECT_DIR / "gallery"
MODELS_DIR  = PROJECT_DIR / "models"

for _d in (DATA_DIR, OUT_DIR, GALLERY_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

GALLERY_PATH = GALLERY_DIR / "gallery.npz"
ZONES_CONFIG_PATH = OUT_DIR / "zones_config.json"

# Default thresholds (mirror the marimo defaults).
DEFAULT_DET_CONF = 0.30
DEFAULT_DET_IOU  = 0.50
DEFAULT_FACE_THR = 0.45
# Cosine similarity threshold for OSNet body-ReID matching against the daily
# fingerprint gallery / static enrollment gallery.
#
# History: started at 0.75 — too strict for genuine cross-camera matches (a
# body embedding from a hallway/common-area camera commonly lands 0.5-0.7
# similarity against the checkin-camera fingerprint, given the different
# angle/lighting/distance), so anything without a clean frontal face fell
# back to ReID and got rejected, showing UNKNOWN everywhere except the one
# camera with good face angles. Dropped to 0.60 to fix that — but that
# proved too loose: confirmed false-positive matches (wrong employee
# assigned) and even two different, simultaneously-visible people both
# matched to the same employee in one frame. Settled at 0.65 as a middle
# ground between the two failure modes.
#
# 2026-08-22: lowered to 0.55 (with DEFAULT_REID_MARGIN raised to 0.12) on
# actual measurements rather than feel. That earlier 0.60 experiment failed
# for reasons that had nothing to do with the threshold: the body model was
# running on ImageNet weights (see REID_WEIGHTS_PATH), crops were taken from
# a 3x-downscaled frame, and two employees were enrolled from identical
# photos — so scores clustered around 0.55 for everyone and margins were
# ~0.01. With those fixed, correct matches score 0.58-0.67 with margins of
# 0.17-0.31 while wrong ones stay ambiguous.
#
# Validated by replaying debug_identity CSVs from three different camera
# geometries (ceiling check-in, reception fisheye, office lobby): at
# 0.55/0.12 every enrolled employee present was identified and every
# unenrolled person stayed UNKNOWN — no false positives. Absolute cosine is
# systematically depressed across viewpoints even for the correct person,
# so the margin is the load-bearing test and the threshold is mostly a
# noise floor.
#
# Caveat for anyone retuning: that validation covered TWO enrolled
# employees. The margin test gets harder as the roster grows, because the
# nearest wrong match gets closer. Re-derive from a debug CSV — never from
# feel — once more people are enrolled, and use reid_thr_by_camera for any
# camera whose distribution turns out to differ.
#
# Also note this threshold is no longer the only line of defence: crops are
# quality-gated first (quality.py), the margin below must also be cleared,
# a track needs PROVISIONAL_MIN_SCORE worth of agreeing frames before a
# name is even shown, and one-employee-per-frame is enforced structurally
# by IdentityFuser.resolve_frame().
DEFAULT_REID_THR = 0.55

# ── Open-set rejection margins ───────────────────────────────────────────────
# A similarity threshold alone cannot separate "this is employee X" from
# "this is nobody in the gallery": nearest-neighbour lookup always returns
# SOMEONE, so an uninformative embedding (back of head, motion blur, a
# visitor who isn't enrolled at all) gets accepted as soon as noise nudges
# its best score over the line. These require the winner to also beat the
# runner-up by a real gap — the signature of a genuine match is "clearly
# closer to X than to anyone else", whereas a garbage crop is roughly
# equally unlike everybody and produces a near-zero margin.
#
# ReID's margin is the larger of the two: body embeddings are far more
# clustered than faces (two colleagues in similar dark jackets sit close
# together in OSNet space), so the gap between #1 and #2 is naturally
# smaller and needs a stricter bar to mean anything.
# 0.12 rather than the original 0.06, from measured distributions across
# three camera geometries: correct cross-camera matches showed margins of
# 0.17-0.31, while unenrolled people produced 0.02-0.15 AND — the more
# telling signal — disagreed with themselves, their winner flipping between
# employees from frame to frame. 0.12 sits in the gap. An enrolled person's
# track is unanimous across every frame; an unenrolled one never is.
DEFAULT_REID_MARGIN = 0.12
DEFAULT_FACE_MARGIN = 0.04

# How many of the best-matching reference vectors per employee are averaged
# to produce that employee's similarity score (see quality.topk_similarity).
REID_TOPK = 3

# ── Crop quality gate (see quality.py) ───────────────────────────────────────
IDENTITY_MIN_CROP_H = 64        # hard reject below this crop height, in px
IDENTITY_SOFT_CROP_H = 120      # penalised (not rejected) below this height
# Crop aspect ratio (h/w) is a WEAK signal and nearly useless as a quality
# measure, which cost real recognition before this was recalibrated. The
# original range (1.2–6.0) encoded "a standing person is clearly taller than
# wide" — true at eye level, false for a steeply-angled ceiling camera, where
# a person is foreshortened to almost square. On the check-in camera the
# measured aspects run 0.91–1.61 with a MEDIAN of 1.06, so that range
# hard-rejected 60% of all frames and lost the second employee entirely.
#
# Aspect now only catches boxes that cannot be one upright person at any
# camera angle: a wide band (two people merged into one detection, or a
# sliver clipped by the frame edge) or an implausibly thin column. Anything
# in between is left to the signals that actually measure crop *quality* —
# blur, pixel height, frame truncation — which don't depend on where the
# camera is mounted. There is deliberately no soft/penalty band any more:
# penalising a whole camera's normal geometry is just a hidden per-camera
# threshold shift, and per-camera thresholds belong in config, explicitly.
IDENTITY_ASPECT_HARD_RANGE = (0.6, 8.0)
IDENTITY_BLUR_HARD = 25.0       # Laplacian variance: below → unusable
IDENTITY_BLUR_SOFT = 60.0       # below → penalised
IDENTITY_EDGE_MARGIN_PX = 2     # box within this many px of a frame border = truncated
IDENTITY_QUALITY_PENALTY = 0.05  # threshold increase per degradation flag

# ── Vote weights and commitment (see identity.py) ────────────────────────────
FACE_VOTE_WEIGHT = 2.0
REID_VOTE_WEIGHT = 1.0
UNKNOWN_VOTE_WEIGHT = 0.5

# Minimum accumulated vote score before a name is even *displayed* for an
# uncommitted track. Previously any single-frame ReID hit immediately became
# the track's label (the vote winner was returned regardless of score), so a
# momentary false match was drawn on the annotated video and fed to the event
# engine before the vote had a chance to settle.
PROVISIONAL_MIN_SCORE = 4.0

# Bar for *committing* a track to an identity. The old rule (score >= 5,
# ratio > 0.55) was reachable by five consecutive ReID-only frames — about
# 0.4 s at stride 2 — and commitment was irreversible, so half a second of
# bad body matching in a badly-framed corner permanently mislabelled a
# person for the entire clip. Body evidence alone can now carry a
# provisional label but can never commit one; a face match is required,
# because faces are the only signal in this system with enough
# discriminative power to be trusted as the last word.
COMMIT_MIN_SCORE = 8.0
COMMIT_MIN_RATIO = 0.60
COMMIT_REQUIRE_FACE = True

# Exception to COMMIT_REQUIRE_FACE, for the architecture this system actually
# uses: employees are enrolled with FACE PHOTOS ONLY. There is no
# enrollment-time body bank, because clothing changes daily and a
# months-old outfit is worse than useless as a reference. Instead the
# check-in camera — the one place a face is reliably visible — face-identifies
# each employee and captures their body fingerprint for TODAY (see
# daily_gallery.py), and every other camera matches bodies against that.
#
# So on a zone camera a face match may never happen, and requiring one would
# mean no track there ever commits: labels would stay provisional forever,
# and track re-linking (which only considers committed tracks as donors)
# could never fire. But a match against a *daily* fingerprint is not
# faceless evidence — that fingerprint exists only because a face was
# recognised at check-in, so the face check has already happened, once, at
# the point where it was actually possible. This is the bar for inheriting
# that confirmation: a higher vote score than a face match needs, because
# the evidence is one step removed.
#
# Matches against the static enrollment bank get no such exception: nothing
# vouches for those, which is why they can only ever label provisionally.
COMMIT_MIN_SCORE_DAILY_BODY = 14.0

# A committed track is no longer final: if some other name out-votes the
# committed one by this much AND has face evidence behind it, the track
# switches. Recovery path for a wrong commit that used to be permanent.
SWITCH_MIN_MARGIN = 4.0

# ── Per-frame identity assignment (see identity.py resolve_frame) ────────────
# Added to a face-derived similarity when building the assignment matrix, so
# that when a face match and a body match compete for the same name the face
# always wins regardless of raw cosine values (the two scores come from
# different models and aren't comparable on their own).
FACE_ASSIGN_BONUS = 1.0

# Score a committed track gets for keeping its own name in the assignment,
# even on frames where it produced no fresh evidence. Keeps labels stable
# through occlusions, while staying below FACE_ASSIGN_BONUS so that another
# track with actual face evidence for that name can still take it away —
# which is how a wrong commit gets corrected instead of blocking the real
# employee (the old revoke()/blacklist behaviour).
STICKY_ASSIGN_SCORE = 0.70

# Minimum cosine similarity between a new track's body embedding and the
# last body embedding of the vacated track it would inherit an identity
# from. Spatiotemporal proximity alone (the previous rule) is at its least
# reliable exactly where it fires most: a doorway or corner, where a stream
# of different people pass through the same few hundred pixels within
# seconds of each other. Requiring the appearance to match too is what
# distinguishes "the same person ByteTrack briefly lost" from "the next
# person to walk through that door".
RELINK_MIN_APPEARANCE_SIM = 0.55

# ── ReID model weights ───────────────────────────────────────────────────────
# torchreid's `build_model(..., pretrained=True)` for OSNet downloads
# **ImageNet classification** weights — see `pretrained_urls` in
# torchreid/reid/models/osnet.py. Those features were never trained to tell
# one person from another, and it shows: on the reception camera the correct
# employee scored 0.589 while the WRONG employee scored 0.579. A 0.01 gap is
# not a threshold problem, it's a model problem — everybody looks alike, so
# no threshold can separate them and lowering one just turns identification
# into a coin flip (which is what the 0.60 experiment in DEFAULT_REID_THR's
# history actually was).
#
# Person-ReID weights (Market1501 / MSMT17 / DukeMTMC) are a separate
# download from torchreid's MODEL_ZOO. Drop the .pth in models/ and it is
# picked up automatically; see ReIDEmbedder for the loading and for the
# warning emitted when it's missing.
# Which checkpoint to prefer, and why THIS one: our situation is
# cross-domain (our own office cameras, no labelled data of our own to train
# on) and cross-camera (check-in camera → reception/zone cameras). torchreid's
# "same-domain" Market1501 checkpoints are trained AND evaluated on one
# dataset, so their headline 94.2 Rank-1 does not transfer here. The
# domain-generalization models do much better off-domain — osnet_ain_x1_0
# trained on MSMT17 with combineall reaches 70.1 Rank-1 / 43.3 mAP on an
# unseen dataset (msmt17 → market1501) versus resnet50's 46.3 — and it is
# trained with COSINE distance, which is exactly the metric this service
# uses. Hence osnet_ain_x1_0 rather than plain osnet_x1_0.
#
# The architecture name and the checkpoint must agree: torchreid's
# load_pretrained_weights() silently skips layers whose names or shapes
# don't match, so pointing REID_WEIGHTS_PATH at an osnet_x1_0 checkpoint
# while REID_MODEL_NAME says osnet_ain_x1_0 would load partial garbage. It
# logs which layers were discarded — read that on first startup.
REID_MODEL_NAME = os.environ.get("REID_MODEL_NAME", "osnet_ain_x1_0")
REID_WEIGHTS_PATH = Path(
    os.environ.get("REID_WEIGHTS_PATH", MODELS_DIR / "osnet_ain_x1_0_msmt17.pth")
).expanduser()
REID_WEIGHTS_AVAILABLE = REID_WEIGHTS_PATH.exists()

# Extract identity crops from the ORIGINAL frame rather than the
# DETECTION_MAX_DIM-downscaled one. Detection is happy at 1280 — YOLO
# resizes internally anyway — but identity is not: on 4K footage that
# downscale left people 111-138 px tall, which OSNet then UPSCALES to its
# 256x128 input, so the embedding is computed from invented pixels. Cropping
# from the native frame costs one extra slice per detection and no extra
# inference. Set false only to reproduce older runs.
IDENTITY_CROPS_AT_NATIVE_RES = True

# ── ReID preprocessing version ───────────────────────────────────────────────
# Bumped whenever the pixel-level preprocessing applied before a ReID
# embedding changes, because a stored bank and a live query MUST have gone
# through identical preprocessing — otherwise every cosine similarity is
# quietly depressed and the whole system drifts toward UNKNOWN with no error
# anywhere to explain why.
#
#   1 — raw BGR crop straight into OSNet, ImageNet weights (original)
#   2 — quality.normalize_illumination() first (gray-world + CLAHE)
#   3 — as 2, but with person-ReID weights loaded (REID_WEIGHTS_PATH)
#
# Version 3 is selected automatically by the presence of the weights file, so
# adding or removing that file invalidates the stored banks by itself. Without
# this, dropping in ReID weights would leave gallery.npz full of embeddings
# from a completely different model, silently compared against new ones.
#
# gallery.npz and gallery/daily/<date>.npz both record the version they were
# built with. A stale static gallery is rebuilt automatically from the images
# still on disk under gallery/<name>/; stale daily fingerprints are discarded
# (their source crops aren't kept), so re-run /checkin/video-multi for that
# date to regenerate them.
REID_PREPROC_VERSION = 3 if REID_WEIGHTS_AVAILABLE else 2

# YOLO's "person" detections use DEFAULT_DET_CONF (0.30) — a low bar is fine
# there since a person-shaped false positive is rare and ByteTrack/IdentityFuser
# provide further downstream filtering. But "cell phone"/"laptop"/"monitor" are
# stock COCO classes never tuned for this domain, and at 0.30 they're easily
# confused with visually-similar background objects — a metal dish rack read
# as "cell phone", a boxy floor-cleaning robot read as "monitor" (both seen in
# real annotated-video output). These behavior-relevant classes need a much
# higher bar before they're trusted enough to even be drawn/considered.
DEFAULT_BEHAVIOR_OBJ_CONF = 0.60
DEFAULT_FUSE_WIN = 30
DEFAULT_STRIDE   = 2
DEFAULT_MAX_FRAMES = 600
DEFAULT_PROX_PX  = 180

# Cap detection resolution — our NVR footage runs up to 3840x2160, but YOLO's
# own preprocessing resizes to a much smaller imgsz internally regardless, so
# feeding it (and face/reid cropping) full 4K buys zero accuracy and directly
# caused a real 502 timeout on /checkin/video-multi (CPU-only inference on a
# multi-minute 4K clip, no early exit). Frames wider/taller than this on their
# longest side are downscaled once per frame before detection; zones and
# emitted bboxes stay self-consistent since everything downstream of the
# resize (zone matching, cropping, annotated-video output) operates in this
# same scaled coordinate space. Does NOT apply to checkin_video()'s
# motion/blur gates — those thresholds were GA-tuned against native-resolution
# footage (see GA optimisation/) and downscaling would need a separate re-tune.
DETECTION_MAX_DIM = 1280

# Annotated debug video (write_video=true on /events/run) writes only 1 out
# of every ANNOTATE_STRIDE *processed* frames to the output — a human
# reviewing footage to sanity-check tracking/identity doesn't need every
# frame, just enough temporal density to follow people moving between
# zones and confirm labels are stable. This is the single biggest lever on
# output file size (Nx fewer frames written ≈ ~Nx smaller before any
# codec-level compression), on top of the DETECTION_MAX_DIM downscale which
# already caps the frame resolution itself.
#
# Playback length is (frames_processed / ANNOTATE_STRIDE) /
# DEFAULT_ANNOTATE_OUTPUT_FPS. At the original 5 a 150s clip became a 31s
# video, which turned out to be too compressed to actually review: a person
# crossing reception in 5s got ~12 written frames, so tracking continuity and
# label stability — the whole reason to look at the video — were hard to
# judge, and the reviewer was reduced to pausing constantly. 2 gives 2.5x
# longer output (that same clip becomes ~78s) at 2.5x the file size, which is
# the better trade for footage that is minutes long, not hours.
#
# Pass annotate_stride=1 per request for effectively real-time playback
# (1875 processed frames / 12 fps ≈ 156s for a 150s clip), or raise it again
# for a quick skim of a long recording.
DEFAULT_ANNOTATE_STRIDE = 2

# Output frame rate for the annotated debug video. Earlier this was computed
# to compensate for ANNOTATE_STRIDE (out_fps = (fps/stride)/annotate_stride)
# so playback duration matched the source clip's real elapsed time — but that
# meant subsampling only reduced smoothness, not length: a 2-minute clip
# stayed a ~2-minute annotated video no matter how high annotate_stride was
# set. Using a fixed fps instead makes duration shrink proportionally with
# annotate_stride (a genuine time-lapse, faster to skim). Each frame already
# has its real timestamp burned in (see _annotate_frame in pipeline.py), so
# nothing is lost — just watched compressed rather than in real time.
DEFAULT_ANNOTATE_OUTPUT_FPS = 12.0

YOLO_WEIGHTS = str(PROJECT_DIR / "yolov8m.pt")
