# data/

Local working copy of shared test media (raw camera clips, test photos). This folder is
gitignored — its contents are never committed — but shared across the team via a shared
Google Drive/Dropbox folder instead.

## Getting the files

1. Get access to the shared folder: **[Dropbox — hr-vision-system test media](https://www.dropbox.com/scl/fo/yr33e9z8035vzz8p7co24/AMfdGxo9GhYkMP_xdWJ80uY?rlkey=7teui5nga78k45qqfjim32u60&st=bvxyf2qc&dl=0)**
2. Download what you need into this folder, keeping the same filenames as the shared
   folder so paths referenced in tickets/notes/API calls stay valid for everyone, e.g.:
   ```
   vision-service/data/Camera_01_NVR_20260405181900_20260405182130_632793.mp4
   ```
3. Reference these as local paths when calling the API, e.g. `/checkin/video`'s `source`
   field or `/events/run`'s `video_paths` — both resolve plain local paths under `data/`
   directly (see `service/storage.py`).

## What's in the shared folder

| File | Content |
|---|---|
| `Video Project 2.mp4` | Checkin video — employees pass by a camera and get recorded for the checkin service. Also the source footage used for the GA frame-filtering tuning (see `docs/GA-OPTIMIZATION-RESULTS.md`). |
| `Camera_01_NVR_20260405181955_20260405182100_1043314.mp4` | Offices lobby — usually used to sit and chat. |
| `Camera_01_NVR_20260405181955_20260405182100_1042642.mp4` | Kitchen area — employees sit and eat, or prepare coffee/drinks. |
| `Camera_01_NVR_20260405181900_20260405182130_632793.mp4` | Main entrance hall/reception — employees and visitors pass through; also captures people leaving. |
| `Camera_01_NVR_20260405181925_20260405182017_633906.mp4` | Main hall — records people passing through. |
| `Camera_01_NVR_20260405181900_20260405182100_1042825.mp4` | Main working area / employee desks. |
| `Camera_01_NVR_20260405181900_20260405182017_633282.mp4` | Another hall view — records people passing by. |

## Adding new test media

If you add new source video/photos during development, upload them to the same shared
folder so teammates can pull them down too — don't just leave them local-only, and don't
commit them to git (large binaries bloat the repo for everyone on every clone).

## What doesn't belong here

- `outputs/` (annotated output videos) — fully regenerated locally by re-running the
  pipeline with `write_video=true`. No need to store or share these.
- `gallery/` — built locally by calling `/enroll`; it's derived state, not source media.
