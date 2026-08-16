# Improvements & TODO

Tracked improvements identified during development. Ordered roughly by priority.

---

## ✅ Done

### Frame-filtering thresholds tuned with a genetic algorithm
`checkin_video()`'s stride → motion gate → blur gate → early-exit funnel was hand-tuned guesswork.
A GA (elitist selection, single-point crossover, Gaussian mutation, 5 seeds) was run against real
InsightFace/pipeline measurements on real footage to tune `motion_thr`, `blur_thr`, and
`early_exit_conf`. Applied to `service/pipeline.py` and `service/routers/checkin.py`:

- `motion_thr`: `0.01` → `0.013`
- `blur_thr`: `80.0` → `183.0`
- `early_exit_conf`: `0.90` → `0.77`
- `stride`: unchanged at `5` — the GA converged there too, confirming it was already reasonable

Result: ~6.6x fewer frames sent to expensive face/ReID inference for a ~1.5% drop in coverage,
verified live against real NVR footage. Full methodology and numbers in `GA-OPTIMIZATION-RESULTS.md`
(project root, one level up).

### Enroll from URL / S3
`POST /enroll` now accepts the same source types as `/checkin` and `/events/run` — local path,
`https://` URL, or `s3://` URI — for both `face_images` and `body_images`.

---

## 🔴 High priority

### 1. Stream support in `/events/run`
The pipeline can almost handle RTSP/webcam streams today — three small gaps block it:

- `Path(video_path).exists()` check blows up on `rtsp://` URLs → skip for non-local sources
- `load_zones_for_video(vp.name, ...)` uses the filename for zone lookup → use `camera_id` instead
- `fps = cap.get(cv2.CAP_PROP_FPS)` returns 0 for live streams → add a configurable fallback fps

Once fixed, callers can pass an RTSP URL with `max_frames` as the processing window:
```json
{
  "video_paths": ["rtsp://192.168.1.100:554/stream1"],
  "max_frames": 900
}
```
This covers "snapshot N seconds of CCTV every minute" without a full architectural change.

---

### 2. Persistent job store
The current job store is in-memory (`jobs.py`). All running/completed jobs are lost on service restart.

- Replace with a SQLite-backed or Redis-backed store
- Jobs should survive restarts and be queryable after the fact
- Add a `GET /events` endpoint to list recent jobs

---

### 3. Uploaded file cleanup
Files saved by `POST /files/upload` are never deleted. A long-running service will fill the `data/` directory.

- Add a TTL (e.g. 24 hours) after which uploaded files are purged
- Or delete the file automatically after the job that consumed it completes
- Add a `DELETE /files/{filename}` endpoint for manual cleanup

---

## 🟡 Medium priority

### 4. Continuous stream monitoring (WebSocket / SSE)
For real-time use cases (sub-minute latency), the request/poll model doesn't fit. Needs a new endpoint:

- `GET /events/stream?camera_id=entrance` — Server-Sent Events (SSE)
- Service reads from RTSP continuously in a background thread
- Pushes events to connected clients as they are detected
- Different architecture from the current batch job model — scope separately

---

### 5. Per-camera FPS configuration
Streams don't always report FPS correctly. Currently falls back to `25.0` which affects all
timestamp calculations in events.

- Add optional `fps_override` per video/stream in the request body
- Or auto-detect from the first N frames

---

### 6. UNKNOWN identity tracking
All unrecognised people share a single `"UNKNOWN"` label. This means two different strangers in
the same corridor appear as the same person in the event log.

- Use the `track_id` from ByteTrack to differentiate unknowns: `"UNKNOWN_42"`, `"UNKNOWN_17"`
- Makes interaction events between unknowns meaningful
- Presence duration per unknown individual becomes trackable

---

### 7. S3 dependency is optional but not guarded
`service/storage.py` raises a clear error if `boto3` is missing, but there's no way to know
upfront whether S3 URIs will work without trying one.

- Add a `GET /health` detail that lists which optional integrations are available
  (`s3: true/false`, `gpu: true/false`)

---

## 🟢 Nice to have

### 8. Event deduplication across overlapping jobs
If a caller submits overlapping time windows for the same camera, events near the boundary
will be double-counted.

- Add optional `start_ts` / `end_ts` metadata to jobs so the caller can detect overlap
- Or add server-side deduplication by `(camera_id, employee_id, event_type, ~start_s)`

### 9. Annotated video for streams
`write_video=true` currently saves to `outputs/{stem}_annotated.mp4`. For streams,
`Path("rtsp://...").stem` produces a garbage filename.

- Use `camera_id` + timestamp as the output filename instead
