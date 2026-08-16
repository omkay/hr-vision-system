# HR Vision System

An integration between an HR management backend and a computer-vision microservice for
automated employee attendance and activity tracking (presence, working, phone use,
interaction) from camera footage.

## Structure

- **`backend-service/`** — Laravel 12 HR backend (employees, zones, cameras, attendance,
  roles/auth). Talks to the vision service over HTTP for enrollment, checkin, and camera
  video processing.
- **`vision-service/`** — Python/FastAPI computer-vision microservice. Face recognition
  (InsightFace) + body re-identification (OSNet/TorchReID) with a fusion layer that prefers
  returning "unknown" over a wrong guess, plus GA-tuned frame filtering for efficiency.
- **`frontend/`** — a minimal single-file HTML/JS test UI (`test-ui.html`) for exercising
  the full flow (zones, employees, photo enrollment, cameras, processing, checkin) without
  a frontend build step. Also present at `backend-service/public/test-ui.html` so it can be
  served directly by the Laravel container.

## Running it

```bash
docker compose up -d --build
```

This starts MySQL, Redis, the Laravel app (`hr-app`, port 8080), a queue worker
(`hr-queue`), and the vision service (`vision-service`, port 8000).

Once it's up, open `frontend/test-ui.html` directly in a browser (or
`http://localhost:8080/test-ui.html`) to exercise the system end to end. Default API base
URL is `http://localhost:8080/api`.

## Test media

Raw test videos/photos are shared via a team Drive/Dropbox folder, not git (large
binaries). See `vision-service/data/README.md` for the link and download instructions.

## Documentation

- `backend-service/openapi.yaml` — full API spec for the Laravel backend.
- `docs/ADR-001-employee-activity-integration.md` (and the Arabic version) — architecture
  decision record for the integration.
- `docs/architecture-diagram.png` — rendered system architecture diagram.
- `docs/GA-OPTIMIZATION-RESULTS.md` — methodology and measured results for the
  genetic-algorithm-tuned frame-filtering defaults applied to the checkin pipeline.
- `docs/MANUAL-TESTING-GUIDE.md` (and the Arabic version) — manual end-to-end test steps
  for enrollment, checkin, and zone-based activity tracking.
- `vision-service/TODO.md` — known gaps and roadmap for the vision service.
