# Manual Testing Guide — Vision Integration Features

Step-by-step instructions to manually test enrollment, attendance checkin, and zone-based activity tracking. Assumes the Docker stack is up (`docker compose up -d` from the project root) and you have `curl` and `python3` available in a terminal.

Replace `admin`/`password123` with your real credentials if different.

## 0. Log in and get a token

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/api/login \
  -H "Accept: application/json" -H "Content-Type: application/json" \
  -d '{"user_name":"admin","password":"password123"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
echo $TOKEN
```

Every request below needs `-H "Authorization: Bearer $TOKEN"` and `-H "Accept: application/json"`. Tokens expire after 60 minutes idle — re-run this if you get a session-expired error.

## 1. Multi-photo enrollment

**Create a test employee** (or use an existing `id`):

```bash
curl -s -X POST http://localhost:8080/api/employees/add \
  -H "Accept: application/json" -H "Authorization: Bearer $TOKEN" \
  -F "name=Test Employee" -F "Administration=IT" -F "job_num=9999" \
  -F "position=Engineer" -F "start_date=2026-08-11" -F "start_time=09:00" -F "end_time=17:00"
```

Note the returned `id` (call it `EMPLOYEE_ID`).

**Upload multiple face/body photos:**

```bash
curl -s -X POST http://localhost:8080/api/employees/EMPLOYEE_ID/photos \
  -H "Accept: application/json" -H "Authorization: Bearer $TOKEN" \
  -F "face_images[]=@/path/to/face1.jpg" \
  -F "face_images[]=@/path/to/face2.jpg" \
  -F "body_images[]=@/path/to/body1.jpg"
```

This dispatches enrollment automatically — wait ~5-10 seconds, then check it landed:

```bash
docker compose exec vision-service python3 -c "
import numpy as np
z = np.load('/app/gallery/gallery.npz', allow_pickle=True)
print('enrolled names:', z['names'].tolist())
"
docker compose exec vision-service ls /app/gallery/9999/face/   # should show both face photos
docker compose exec vision-service ls /app/gallery/9999/body/   # should show the body photo
```

(`9999` is the `job_num` — that's the identity key in the vision service, not the Hr_SmartPay `id`.)

**Test deletion actually removes the image:**

```bash
curl -s -X DELETE http://localhost:8080/api/employees/EMPLOYEE_ID/photos/PHOTO_ID \
  -H "Accept: application/json" -H "Authorization: Bearer $TOKEN"
# wait ~5s, then re-check:
docker compose exec vision-service ls /app/gallery/9999/face/   # should now show only one photo
```

If something fails, check `docker compose logs hr-queue` — enrollment runs as a background queued job, not inline.

## 2. Attendance checkin

Uses whichever photo you enrolled above.

```bash
curl -s -X POST http://localhost:8080/api/checkin \
  -H "Accept: application/json" -H "Authorization: Bearer $TOKEN" \
  -F "photo=@/path/to/face1.jpg"
```

Expected: `201` with a checkin record if the photo matches an enrolled employee, `422` if unrecognized.

**Try it again immediately** — should now return `409` "already checked in today":

```bash
curl -s -X POST http://localhost:8080/api/checkin \
  -H "Accept: application/json" -H "Authorization: Bearer $TOKEN" \
  -F "photo=@/path/to/face1.jpg"
```

**View the checkin log:**

```bash
curl -s http://localhost:8080/api/checkins -H "Accept: application/json" -H "Authorization: Bearer $TOKEN"
# optionally filter: ?employee_id=EMPLOYEE_ID or ?date=2026-08-11
```

If a checkin call hangs for ~30 seconds and then fails, check that `PHP_CLI_SERVER_WORKERS=4` is set on the `hr-app` service in `docker-compose.yml` and that you rebuilt after any docker-compose.yml changes (`docker compose up -d hr-app`).

## 3. Zone-based activity tracking

This is the slowest to test — real video processing takes a few minutes on CPU.

**Create a zone** (if you don't have one):

```bash
curl -s -X POST http://localhost:8080/api/zone/add \
  -H "Accept: application/json" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Main Warehouse","zone_type":"common_area"}'
```

**Upload a camera video.** Important: the file must be a properly-encoded MP4/MOV (check with `file yourvideo.mp4` — it should say "ISO Media" or similar, not "data"). Raw NVR exports sometimes fail Laravel's MIME sniffing.

```bash
curl -s -X POST http://localhost:8080/api/camera/add \
  -H "Accept: application/json" -H "Authorization: Bearer $TOKEN" \
  -F "name=Entrance Camera" -F "zone_id=ZONE_ID" -F "video=@/path/to/video.mp4"
```

Note the returned camera `id` (`CAMERA_ID`).

**Trigger processing:**

```bash
curl -s -X POST http://localhost:8080/api/camera/CAMERA_ID/process \
  -H "Accept: application/json" -H "Authorization: Bearer $TOKEN"
```

Returns immediately with a `vision_job_id` and status `running` — the actual video analysis happens in the background.

**Watch progress directly on the vision service** (optional, for visibility):

```bash
curl -s http://localhost:8000/events/VISION_JOB_ID | python3 -m json.tool
```

`progress` climbs from 0 to 1.0. This can take several minutes depending on video length — the Hr_SmartPay polling job checks back every ~10-120 seconds (backoff), so results may lag a bit behind the vision service actually finishing.

**Check the job status in Hr_SmartPay's DB:**

```bash
docker compose exec mysql mysql -uroot -proot hr_smartpay \
  -e "SELECT id, status, error_message FROM vision_jobs;"
```

Once `status` is `done`, fetch the results:

```bash
curl -s http://localhost:8080/api/camera/CAMERA_ID/events \
  -H "Accept: application/json" -H "Authorization: Bearer $TOKEN"
# optional filters: ?employee_id=..., ?event_type=presence|working|phone_use|interaction, ?date=...
```

Each event shows `employee_name`/`job_num` if the vision service matched someone enrolled, or `null` if it saw an unrecognized person.

**Batch trigger** (multiple cameras in one job):

```bash
curl -s -X POST http://localhost:8080/api/cameras/process-batch \
  -H "Accept: application/json" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"camera_ids": [1, 2]}'
```

## Troubleshooting

- **`docker compose ps` shows nothing running**: `docker compose up -d --build` from the project root (rebuild picks up any code changes since the images were last built).
- **Login fails**: check the `users` table has your account — `docker compose exec mysql mysql -uroot -proot hr_smartpay -e "SELECT * FROM users;"`.
- **Anything queue-related seems stuck**: `docker compose logs hr-queue` shows every job attempt with RUNNING/FAIL/DONE and timing — this is the fastest way to see what's actually happening.
- **Vision service errors**: `docker compose logs vision-service` — Python tracebacks show up here directly.
