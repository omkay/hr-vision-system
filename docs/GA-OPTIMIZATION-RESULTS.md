# GA Optimization Applied to `/checkin/video` Frame Sampling

Source: `GA optimisation/` (coursework notebooks — `GA_Frame_Sampling.ipynb`, `GA_RealVideo.ipynb`, `GA_CheckinVideo.ipynb`, `GA_CheckinVideo_marimo.py`). This document records how that GA was actually wired into `employee_activity_tracker_2026`, what had to be fixed to make it valid, and the tuned values now live in the service.

## What the GA optimizes

`checkin_video()` (`service/pipeline.py`) runs a three-stage filter funnel before expensive face/ReID inference: **stride** (process every Nth frame) → **motion gate** (skip static frames) → **blur gate** (skip out-of-focus frames) → inference, with an **early exit** once a confident-enough match is found. The GA tunes these four parameters — `stride`, `motion_thr`, `blur_thr`, `early_exit_conf` — to maximize a fitness function balancing match coverage against how few frames get sent to inference:

```
fitness = 0.6 * coverage + 0.4 * (1 - frames_processed / frames_read)
```

## The scale mismatch that had to be fixed first

The coursework notebooks computed `motion`/`blur` on frames **downscaled to 320×180**, and used `mean(absdiff)/255` for motion — neither of which matches what `pipeline.py` actually does: motion is `count_nonzero(diff > 25) / diff.size` and blur is `cv2.Laplacian(...).var()`, both computed on the **full native-resolution frame**. That's why the coursework's tuned `blur_thr` (~1200, calibrated for downscaled frames) was nowhere near the service's real default of `80.0` — they were never comparable numbers. The coursework's `face_conf` was also a Haar-cascade proxy, not the real InsightFace detector the service actually uses.

Fixed by writing `extract_ga_features.py`, run inside the vision-service container so it uses the real `FaceEmbedder`/InsightFace detector at full resolution with the exact motion/blur formulas from `pipeline.py`, against `data/Video Project 2.mp4` (the same source video the original coursework used). Produced `real_video_data_full_res.json` — 681 samples (every 5th of 3402 frames, 1920×1080), 137 with a real detected face (confidence range 0.52–0.91).

## Re-running the GA on corrected data

`run_ga_checkin.py` re-implements the same hand-rolled GA (elitist selection, single-point crossover, Gaussian mutation, population 100, 80 generations) with bounds corrected to the real measurement scale (`blur_thr` searched over 30–400, not 500–5000). Run across 5 seeds for stability, same as the original methodology:

| seed | fitness | stride | motion_thr | blur_thr | early_exit_conf |
|---|---|---|---|---|---|
| 42 | 0.9040 | 1 | 0.0132 | 182.9 | 0.774 |
| 7 | 0.9037 | 1 | 0.0147 | 185.9 | 0.708 |
| 99 | 0.9040 | 1 | 0.0132 | 183.0 | 0.709 |
| 123 | 0.9040 | 1 | 0.0133 | 183.1 | 0.698 |
| 256 | 0.9022 | 1 | 0.0153 | 45.6 | 0.803 |

Spread across seeds: 0.0018 — stable, same convergence criterion the coursework used.

**Note on `stride`**: the GA's "stride" gene multiplies on top of the dataset's own pre-sampling (every 5th native frame), so a gene value of 1 corresponds to the service's actual native `stride=5` — the GA converging to that boundary confirms the current stride is already reasonable, not that it should change.

**Corrected baseline comparison** (both at native `stride=5`):

| | fitness | coverage | frames processed / read |
|---|---|---|---|
| Current service defaults (`motion_thr=0.01`, `blur_thr=80`, `early_exit_conf=0.90`) | 0.885 | 0.861 | 53 / 681 |
| GA-tuned (`motion_thr=0.013`, `blur_thr=183`, `early_exit_conf=0.77`) | 0.904 | 0.848 | 8 / 681 |

**~6.6x fewer frames sent to expensive face/ReID inference, for a ~1.5% drop in coverage.** The `early_exit_conf` change is the main driver: real InsightFace confidences on this footage rarely exceeded ~0.9, so demanding 0.90 to exit early forced the scanner to keep reading far more frames than necessary chasing a threshold it would almost never cleanly hit.

## Applied

`service/pipeline.py` (`checkin_video`/`_checkin_video_local` defaults) and `service/routers/checkin.py` (`CheckinVideoRequest` field defaults, with description text explaining the tuning):

- `motion_thr`: `0.01` → `0.013`
- `blur_thr`: `80.0` → `183.0`
- `early_exit_conf`: `0.90` → `0.77`
- `stride`: unchanged (`5`) — the GA validated this rather than pushing for a change

Rebuilt and verified live against real footage (`docker compose exec vision-service` on `data/Camera_01_NVR_...mp4`): with the new defaults, only 6 of 371 read frames were sent to inference (vs. the roughly-53-of-681 ratio measured above), and the correct identity was still matched (`reid` method, confidence 0.82).
