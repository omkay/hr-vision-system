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

## Local setup

### Prerequisites

- **Docker Desktop** — [Mac](https://www.docker.com/products/docker-desktop/) /
  [Windows](https://www.docker.com/products/docker-desktop/). On Windows, make sure the
  **WSL2 backend** is enabled (Docker Desktop → Settings → General → "Use the WSL 2 based
  engine") — the default on any recent install, but worth checking if builds behave oddly.
- **Git**. Mac: usually preinstalled, or `brew install git`. Windows:
  [git-scm.com](https://git-scm.com/download/win) (installs Git Bash, which is the easiest
  shell to run the commands below in).
- Some free disk space — the vision-service image alone pulls PyTorch + OpenCV +
  InsightFace on first build, expect a multi-GB image and a first build that takes several
  minutes.

### 1. Clone the repo

```bash
git clone git@github.com:omkay/hr-vision-system.git
cd hr-vision-system
```

(Windows/Git Bash: identical. If you haven't set up an SSH key with GitHub, use the HTTPS
clone URL from the repo's "Code" button instead.)

### 2. Get the YOLOv8 model weights

`vision-service/yolov8m.pt` (~52MB) is a public pretrained weight file, not committed to
git. Download it and place it directly in `vision-service/`:

- Mac/Linux:
  ```bash
  curl -L -o vision-service/yolov8m.pt https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8m.pt
  ```
- Windows (PowerShell):
  ```powershell
  Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8m.pt" -OutFile "vision-service\yolov8m.pt"
  ```

Check the [ultralytics assets releases page](https://github.com/ultralytics/assets/releases)
if that exact version tag 404s — any recent `yolov8m.pt` release asset works.

### 3. (Optional) Get test video media

If you want to exercise checkin/activity-tracking with real footage instead of your own
test files, see `vision-service/data/README.md` for the shared Dropbox link and what each
video contains.

### 4. Start the stack

```bash
docker compose up -d --build
```

This builds and starts MySQL, Redis, the Laravel app (`hr-app`, port 8080), a queue worker
(`hr-queue`), and the vision service (`vision-service`, port 8000). Migrations run
automatically on `hr-app`'s first boot.

### 5. Seed the database (first time only)

Migrations don't seed any data, so there's no login until you run this once:

```bash
docker compose exec hr-app php artisan db:seed
```

This creates the three roles (HR Manager, HR Employee, CEO) and one login user:
**username `it`, password `It123#321`** (CEO role — full access).

### 6. Open the test UI

Open `frontend/test-ui.html` directly in a browser (double-click it, or
`http://localhost:8080/test-ui.html` once the stack is up) to exercise the full flow —
zones, employees, photo enrollment, cameras, processing, checkin. Default API base URL in
the UI is `http://localhost:8080/api`; log in with the seeded `it` / `It123#321` user.

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
