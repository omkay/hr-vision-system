# Employee Activity Tracking — REST Service

A FastAPI service that wraps the CV pipeline and exposes it over HTTP.
Useful when the notebook workflow is too manual or when you want to integrate
the pipeline into a larger system (dashboard, scheduler, RTSP ingestor, etc.).

---

## Table of contents

1. [Starting the service](#1-starting-the-service)
2. [Architecture overview](#2-architecture-overview)
3. [Endpoints](#3-endpoints)
   - [Health](#get-health)
   - [Enroll](#post-enroll)
   - [Checkin — image](#post-checkin)
   - [Checkin — video / stream](#post-checkinvideo)
   - [Run pipeline job](#post-eventsrun)
   - [Poll job result](#get-eventsjob_id)
4. [Key concepts](#4-key-concepts)
   - [Zones](#zones)
   - [Event types](#event-types)
   - [Frame filtering (checkin_video)](#frame-filtering)
5. [End-to-end tutorial](#5-end-to-end-tutorial)
6. [Tuning reference](#6-tuning-reference)
7. [What was built in this session](#7-what-was-built-in-this-session)

---

## 1. Starting the service

```bash
# From the project root
venv/bin/python -m uvicorn service.app:app --host 0.0.0.0 --port 8000
```

Windows:
```bat
venv\Scripts\python -m uvicorn service.app:app --host 0.0.0.0 --port 8000
```

The interactive API docs (Swagger UI) are available at:
```
http://localhost:8000/docs
```

---

## 2. Architecture overview

```
POST /enroll         ─── builds / updates gallery.npz
                               │
                               ▼
POST /checkin        ─── single-image identity lookup
POST /checkin/video  ─── video / stream identity lookup  (motion + blur gates)
                               │
                               ▼
POST /events/run     ─── submits async background job
                               │
                         ┌─────┴──────────────────────────────────────────┐
                         │  for each video (sequential):                  │
                         │    YOLO + ByteTrack  → detections + track IDs  │
                         │    InsightFace       → face embedding           │
                         │    OSNet / TorchReID → body embedding           │
                         │    IdentityFuser     → employee_id              │
                         │    EventEngine       → events (zones, rules)    │
                         └────────────────────────────────────────────────┘
                               │
GET /events/{job_id} ─── poll status / fetch results
```

Models are **lazy singletons** — loaded once on first use, shared across all requests.

---

## 3. Endpoints

### GET /health

Returns `{"status": "ok"}`. Use this to confirm the service is up.

---

### POST /enroll

Register or update an employee in the gallery.
Must be called before running the pipeline or checkin — the gallery is the
source of truth for employee identities.

**Request body**
```json
{
  "name": "hasan",
  "face_images": ["/abs/path/gallery/hasan/face/01.jpg"],
  "body_images":  ["/abs/path/gallery/hasan/body/01.jpg"]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | ✅ | Unique employee ID / folder name |
| `face_images` | one of | Absolute paths to face photos on the server |
| `body_images` | one of | Absolute paths to full-body photos on the server |

**Response**
```json
{
  "name": "hasan",
  "face_embeddings": 3,
  "body_embeddings": 2
}
```

**Notes**
- The gallery is stored as `gallery/gallery.npz` in the project root.
- Calling enroll again for the same `name` replaces their embeddings.
- You can provide only face photos, only body photos, or both.

---

### POST /checkin

Identify the most prominent person in **a single image**.

**Request body**
```json
{
  "image_path": "/abs/path/to/frame.jpg",
  "face_thr": 0.45,
  "reid_thr": 0.75
}
```

**Response**
```json
{
  "employee_id": "hasan",
  "confidence": 0.977,
  "method": "face"
}
```

| `method` value | Meaning |
|---------------|---------|
| `"face"` | Matched via InsightFace ArcFace embedding |
| `"reid"` | Face not found; matched via OSNet body embedding |
| `"none"` | No match above threshold |

---

### POST /checkin/video

Identify the most prominent person across a **video file or live camera stream**.

Applies a three-stage filter funnel to avoid running expensive models on useless frames:

```
Every Nth frame (stride)
       │
       ▼
[Motion gate]   frame diff < motion_thr  → skip (static / empty scene)
       │
       ▼
[Blur gate]     Laplacian var < blur_thr → skip (motion blur / out of focus)
       │
       ▼
[Face + ReID inference]
       │
       ▼
[Early exit]    confidence ≥ early_exit_conf → return immediately
       │
       ▼
[Vote aggregation]   most-voted identity across all hits
```

**Request body**
```json
{
  "source": "/abs/path/to/video.mp4",
  "stride": 5,
  "motion_thr": 0.01,
  "blur_thr": 80.0,
  "early_exit_conf": 0.90,
  "max_frames": 500
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `source` | — | Video file path, RTSP URL, or webcam index (`"0"`) |
| `stride` | `5` | Process every Nth frame |
| `motion_thr` | `0.01` | Min fraction of pixels that must change (1%) |
| `blur_thr` | `80.0` | Min Laplacian variance for a frame to be considered sharp |
| `early_exit_conf` | `0.90` | Stop scanning as soon as confidence exceeds this |
| `max_frames` | `500` | Hard cap on frames inspected (safety for long streams) |

**Response**
```json
{
  "employee_id": "hasan",
  "confidence": 0.978,
  "method": "face",
  "votes": { "hasan": 23 },
  "frames_read": 431,
  "frames_processed": 25,
  "skipped_motion": 62,
  "skipped_blur": 0,
  "skipped_no_face": 2
}
```

**Live stream example**
```json
{ "source": "rtsp://192.168.1.100:554/stream1", "max_frames": 150 }
```
With `max_frames=150` at stride 5, the endpoint inspects ~30 frames — roughly
5 seconds of a 30 fps stream — then returns.

---

### POST /events/run

Submit an **async background job** that runs the full activity-tracking pipeline
over one or more videos. Returns immediately with a `job_id`.

**Request body**
```json
{
  "video_paths": [
    "/abs/path/corridor_a.mp4",
    "/abs/path/office.mp4"
  ],
  "camera_ids": ["corridor_a", "office_desks"],
  "zones": [
    [{"label": "corridor_a",   "zone_type": "common_area"}],
    [{"label": "shared_desks", "zone_type": "work_area"}]
  ],
  "stride": 2,
  "max_frames": 1500,
  "write_video": false
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `video_paths` | — | List of absolute server-side video paths |
| `camera_ids` | `["cam0", "cam1", …]` | Labels attached to every event from that camera |
| `zones` | `null` | Per-video zone lists (see [Zones](#zones)). `null` = full frame as `work_area` |
| `det_conf` | `0.30` | YOLO detection confidence threshold |
| `det_iou` | `0.50` | YOLO NMS IoU threshold |
| `face_thr` | `0.45` | Min cosine similarity for a face match |
| `reid_thr` | `0.75` | Min cosine similarity for a ReID match |
| `fuse_win` | `30` | Frames over which identity votes are accumulated |
| `stride` | `2` | Process every Nth frame |
| `max_frames` | `600` | Max frames per video |
| `prox_px` | `180` | Pixel distance threshold for `interaction` events |
| `write_video` | `false` | Whether to write an annotated output video |

**Response**
```json
{ "job_id": "af11a8d0ab7d", "status": "running" }
```

---

### GET /events/{job_id}

Poll a job submitted by `/events/run`.

**Response — running**
```json
{ "job_id": "af11a8d0ab7d", "status": "running", "progress": 0.4 }
```

**Response — done**
```json
{
  "job_id": "af11a8d0ab7d",
  "status": "done",
  "result": {
    "events": [ ... ],
    "event_count": 27,
    "annotated_videos": []
  }
}
```

---

## 4. Key concepts

### Zones

A zone is a named rectangular region of the video frame with a `zone_type`
that controls which events are detected inside it.

```json
{
  "label": "shared_desks",
  "zone_type": "work_area",
  "x1": null, "y1": null, "x2": null, "y2": null
}
```

- Omit all coordinates (or set to `null`) to cover the **entire frame**.
- Coordinates are pixel values from the top-left corner of the frame.

| `zone_type` | Events detected |
|-------------|----------------|
| `"work_area"` | `presence`, `working` (laptop + monitor), `phone_use` |
| `"common_area"` | `presence`, `interaction` (proximity between employees) |

**Zones are per-video.** The `zones` field in `/events/run` is a list of lists —
one inner list per video, matching the order of `video_paths`:

```json
"zones": [
  [{"label": "corridor_a", "zone_type": "common_area"}],   // for video_paths[0]
  [{"label": "shared_desks", "zone_type": "work_area"}],   // for video_paths[1]
  null                                                       // for video_paths[2] → full-frame default
]
```

---

### Event types

Every event in the result has this shape:

```json
{
  "camera_id":   "office_desks",
  "employee_id": "majd",
  "event_type":  "working",
  "start_s":     82.6,
  "end_s":       97.5,
  "duration_s":  14.9,
  "zone":        "shared_desks",
  "zone_type":   "work_area",
  "work_proxy":  "laptop+monitor"
}
```

| `event_type` | Trigger condition |
|-------------|-------------------|
| `presence` | Person footpoint inside a zone for ≥ 2 s |
| `working` | Person in a `work_area` zone with both a laptop and monitor in their bounding box for ≥ 3 s |
| `phone_use` | Person bbox overlaps a detected phone for ≥ 2 s |
| `interaction` | Two identified employees within `prox_px` pixels of each other in a `common_area` for ≥ 4 s |

Events shorter than the minimum duration are silently dropped.

---

### Frame filtering

The `/checkin/video` endpoint uses three lightweight gates to skip frames
before any model is invoked:

| Gate | Cost | What it catches |
|------|------|----------------|
| Motion (frame diff) | ~0 ms | Static scenes, empty corridors between footfall |
| Blur (Laplacian var) | ~1 ms | Motion blur, defocus |
| Face detection | ~10 ms | Sharp, moving frames where nobody is facing the camera |

In a corridor test video (113 s, 3402 frames), these gates reduced the frames
that reached full inference from 500 → 25 (**95% skip rate**), bringing
end-to-end identification time from ~60 s down to **4.3 s** with early exit.

---

## 5. End-to-end tutorial

This tutorial walks through a complete workflow: enroll employees, run the
pipeline over NVR footage, and use checkin to identify a person at a camera.

### Step 1 — Start the service

```bash
cd /path/to/graduation\ project
venv/bin/python -m uvicorn service.app:app --host 0.0.0.0 --port 8000
```

### Step 2 — Enroll employees

Prepare face and/or body photos and call `/enroll` once per employee:

```bash
curl -s -X POST http://localhost:8000/enroll \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hasan",
    "face_images": [
      "/abs/path/gallery/hasan/face/01.jpg",
      "/abs/path/gallery/hasan/face/02.jpg"
    ],
    "body_images": ["/abs/path/gallery/hasan/body/01.jpg"]
  }'
```

Repeat for every employee. The gallery is cumulative — each call adds or
replaces that employee's embeddings.

### Step 3 — Identify a person at the door (checkin)

**From a single frame:**
```bash
curl -s -X POST http://localhost:8000/checkin \
  -H "Content-Type: application/json" \
  -d '{"image_path": "/abs/path/frame.jpg"}'
```

**From a checkin camera video clip or live stream:**
```bash
curl -s -X POST http://localhost:8000/checkin/video \
  -H "Content-Type: application/json" \
  -d '{
    "source": "/abs/path/checkin_clip.mp4",
    "early_exit_conf": 0.90,
    "max_frames": 150
  }'
```

Response:
```json
{
  "employee_id": "hasan",
  "confidence": 0.978,
  "method": "face",
  "votes": {"hasan": 23},
  "frames_read": 431,
  "frames_processed": 25,
  "skipped_motion": 62
}
```

### Step 4 — Run the pipeline over NVR footage

Submit a job with all cameras in one request. Each camera gets its own zone type:

```bash
curl -s -X POST http://localhost:8000/events/run \
  -H "Content-Type: application/json" \
  -d '{
    "video_paths": [
      "/abs/path/data/corridor_a.mp4",
      "/abs/path/data/office.mp4",
      "/abs/path/data/lobby.mp4"
    ],
    "camera_ids": ["corridor_a", "office_desks", "main_lobby"],
    "zones": [
      [{"label": "corridor_a",   "zone_type": "common_area"}],
      [{"label": "shared_desks", "zone_type": "work_area"}],
      [{"label": "main_lobby",   "zone_type": "common_area"}]
    ],
    "stride": 2,
    "max_frames": 1500
  }'
```

Response: `{"job_id": "af11a8d0ab7d", "status": "running"}`

### Step 5 — Poll for results

```bash
curl -s http://localhost:8000/events/af11a8d0ab7d
```

Poll every few seconds until `"status": "done"`, then read `result.events`.

---

## 6. Tuning reference

| Parameter | Default | Lower value | Higher value |
|-----------|---------|-------------|--------------|
| `det_conf` | `0.30` | More detections, more false positives | Fewer detections, fewer false positives |
| `face_thr` | `0.45` | More identity matches (risk: wrong ID) | Fewer matches (more UNKNOWN) |
| `reid_thr` | `0.75` | More body matches | Stricter matching |
| `stride` | `2` | Higher fidelity, slower | Faster, may miss short events |
| `max_frames` | `600` | — | Covers more of the video |
| `prox_px` | `180` | Only very close interactions | Larger "interaction radius" |
| `motion_thr` | `0.01` | Catches subtle movement | Only triggers on significant motion |
| `blur_thr` | `80.0` | Accepts blurrier frames | Stricter — only sharp frames pass |
| `early_exit_conf` | `0.90` | Returns on less-certain match | Waits for very high confidence |

---

## 7. What was built in this session

This section summarises the changes made to the service codebase during the
development session on 2026-05-28.

### Zone system — full redesign

**Problem**: Zones were hardcoded inside the pipeline (`DEFAULT_ZONES` constant).
Every video used the same fixed desk/corridor rectangles regardless of what the
camera actually showed.

**What was built**:
- `service/schemas.py` (new file) — `ZoneDefinition` Pydantic model with `label`,
  `zone_type` (`work_area` | `common_area`), and optional pixel coordinates.
- `events_engine.py` — `Zone` dataclass gained a `zone_type` field; event logic
  now uses `zone_type` instead of name-prefix heuristics (`desk_*`).
- `pipeline.py` — resolves `ZoneDefinition` objects into `Zone` objects at
  runtime; falls back to a full-frame zone when none are provided.

### Per-video zones

**Problem**: The first API design applied one shared `zones` list to all videos
equally. Since all zones were full-frame when no coordinates were given, the
engine always picked `work_area` zones first and corridors were mislabelled.

**Fix**: `zones` in the request changed from `List[ZoneDefinition]` to
`List[Optional[List[ZoneDefinition]]]` — one inner list per video, `null` means
"use the full frame default for that video".

### ByteTrack state reset between videos

**Problem**: `YOLO.track(persist=True)` keeps a `BYTETracker` instance alive
across calls. When processing multiple videos sequentially with the same YOLO
singleton, track IDs from earlier videos leaked into later ones — causing
near-zero event detection on the second+ video.

**Fix**: `PersonObjectDetector.reset_tracker()` calls `tracker.reset()` on each
existing `BYTETracker` instance at the start of every `run_pipeline()` call.
This clears tracked/lost/removed stracks, the frame counter, and Kalman filter
state while leaving `predictor.trackers` intact (required for `persist=True`).

### Checkin — video and stream support

**Problem**: `POST /checkin` only accepted a single pre-extracted image, requiring
the caller to do all the frame selection work. This was impractical for checkin
cameras or recorded clips.

**What was built**:
- `_checkin_bgr(img, ...)` — internal helper that runs checkin logic on a numpy
  array (decouples frame loading from inference).
- `checkin_video(source, ...)` — processes a video file or RTSP/webcam stream
  through a three-stage filter funnel:
  1. **Motion gate** — frame differencing, skips static frames (~0 ms).
  2. **Blur gate** — Laplacian variance, skips blurry frames (~1 ms).
  3. **Face + ReID inference** — only runs on sharp, moving frames (~50 ms).
  - Supports **early exit** (stop once confidence ≥ threshold).
  - Aggregates **votes** across frames for robustness.
- `POST /checkin/video` — new endpoint exposing `checkin_video` over HTTP.

**Measured results** on a 113-second test video:
- Motion gate eliminated **74% of inspected frames** before any model ran.
- With early exit at 0.90 confidence, the correct identity was returned after
  reading only **12.7% of the video** in **4.3 seconds**.

### NaN serialisation fix

`pandas` `to_dict()` can produce `float("nan")` values for columns that don't
apply to every event type (e.g., `work_proxy` only exists on `working` events).
`json.dumps` rejects these. Fixed in `_run_all` in `routers/events.py`:
```python
records = df.where(df.notna(), other=None).to_dict(orient="records")
```
