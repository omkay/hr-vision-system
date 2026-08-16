# data/

Local working copy of shared test media (raw camera clips, test photos). This folder is
gitignored — its contents are never committed — but shared across the team via a shared
Google Drive/Dropbox folder instead.

## Getting the files

1. Get access to the shared folder: **[add your Google Drive / Dropbox link here]**
2. Download what you need into this folder, keeping the same filenames/subfolders as the
   shared folder so paths referenced in tickets/notes/API calls stay valid for everyone,
   e.g.:
   ```
   vision-service/data/Camera_01_NVR_20260405181900_20260405182130_632793.mp4
   ```
3. Reference these as local paths when calling the API, e.g. `/checkin/video`'s `source`
   field or `/events/run`'s `video_paths` — both resolve plain local paths under `data/`
   directly (see `service/storage.py`).

## Adding new test media

If you add new source video/photos during development, upload them to the same shared
folder so teammates can pull them down too — don't just leave them local-only, and don't
commit them to git (large binaries bloat the repo for everyone on every clone).

## What doesn't belong here

- `outputs/` (annotated output videos) — fully regenerated locally by re-running the
  pipeline with `write_video=true`. No need to store or share these.
- `gallery/` — built locally by calling `/enroll`; it's derived state, not source media.
