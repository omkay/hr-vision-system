# ADR-001: Integrating the employee_activity_tracker_2026 vision service with Hr_SmartPay

**Status:** Proposed (2nd revision — replaces both prior versions)
**Date:** 2026-07-23
**Deciders:** Omar Khairat
**Context window:** single-site pilot, many cameras, near-real-time (minutes), thesis defense in ~1 month

> **Revision note:** the first two versions of this ADR were written against an old,
> unrelated `Hr_SmartPay` codebase (multi-tenant, buses/trips/payroll, no zones or cameras at
> all). That branch is not what's being integrated. The real target is the `University` branch —
> a small, purpose-built backend already modeling `Employee`, `Zone`, `Camera`, and the
> `employee_zone` assignment. This revision throws out the multi-tenant/transportation framing
> entirely and designs against what's actually there. Nothing from the previous two versions
> carries over except the underlying vision-service API (unchanged) and the Docker packaging
> (updated, see Consequences).

## Context

**`Hr_SmartPay` (`University` branch)** is a single-tenant Laravel 12 app with exactly four
domains — no bus/transportation/payroll/tenancy code exists here at all:

| Model | Shape | Relationships |
|---|---|---|
| `Employee` | name, `job_num` (unique int), position, start_time/end_time (shift), `image` | belongsToMany `Zone` via `employee_zone` |
| `Zone` | just a `name` | hasMany `Camera`, belongsToMany `Employee` |
| `Camera` | name, **an uploaded video file**, belongs to one `Zone` | belongsTo `Zone` |
| `User` / `Role` | simple custom auth, roles are hardcoded strings (HR Manager, CEO, HR Employee) | — |

The single most important fact driving this whole design: **`Camera.video` is an uploaded file**
(`POST /camera/add` takes `multipart/form-data` with an `.mp4`/`.mov`/etc., stored on the
`public` disk), not a live RTSP feed or an NVR export. There is no concept of a continuous
stream anywhere in this app. That changes the integration shape completely from the previous
two ADR drafts, which assumed a GPU box watching live cameras and pushing results in.

**What already exists that the old ADRs assumed didn't:**
- Zones exist as first-class records, already linked to cameras and to employees.
- `Employee.job_num` is already the unique, stable, human-meaningful identifier — no need to
  invent a `vision_key` column like the previous drafts did.
- `Employee.image` is already collected on create/update — a ready-made source for face gallery
  enrollment, no separate photo pipeline needed.

**What's still missing:**
- Nowhere to store vision results — no `activity_events` table, nothing.
- No `type` on `Zone` (`work_area` vs `common_area`) — the vision service's `EventEngine` needs
  this to decide what to detect (presence/working/phone_use vs. presence/interaction); today
  every zone would silently default to full-frame `work_area` if fed straight into `/events/run`.
- No trigger to actually run a camera's video through the pipeline, and nothing to receive the
  result back.
- The vision service still has zero authentication (unchanged from prior revisions — still
  worth fixing before real footage flows through it).

**A pre-existing bug, unrelated to this integration but worth naming:**
`EmployeeController::showProfile()` (routed at `GET /employee/{token}`) queries
`Employee::where('qr_token', ...)`, but this branch's `employees` migration has no `qr_token`
column. That route 500s on every hit. Not something this ADR touches, but don't be surprised by
it during a demo.

## Decision

Reverse the data-flow direction from the previous drafts. There is no separate GPU box pushing
events into Hr_SmartPay anymore — **Hr_SmartPay is the initiator**. An admin uploads a camera's
video (already built), then triggers processing; Hr_SmartPay calls the vision service directly
over the docker network, polls for the result, and stores it. No inbound auth surface on
Hr_SmartPay is needed for this flow at all, which removes an entire category of risk the
previous drafts had to design around.

```
Admin uploads video ──► Camera.video (already built)
        │
        ▼
POST /camera/{id}/process  (new)
        │
        ▼
Hr_SmartPay ──POST /events/run──► vision-service   (existing endpoint, unchanged)
        │  stores job_id, status=running
        ▼
queued job polls GET /events/{job_id}  (existing endpoint, unchanged)
        │  on done:
        ▼
activity_events table  (new) ── resolved via employee_id = Employee::where('job_num', key)
```

Employee photo enrollment follows the same outbound-only pattern:

```
Employee created/updated with `image` ──► POST /enroll on vision-service (existing endpoint)
                                            name = (string) job_num
```

This keeps the vision service exactly as it is today (no new endpoints needed there) and
confines all new complexity to Hr_SmartPay, which is the repo actually being extended.

## Data model (Hr_SmartPay — all new, all additive)

```php
// Migration: add zone_type semantics the vision service already understands.
Schema::table('zones', function (Blueprint $table) {
    $table->string('type')->default('work_area'); // 'work_area' | 'common_area'
});

// Migration: track processing state per camera.
Schema::table('cameras', function (Blueprint $table) {
    $table->string('vision_job_id')->nullable();
    $table->string('processing_status')->default('idle'); // idle | queued | running | done | error
    $table->timestamp('processed_at')->nullable();
});

// Migration: where results land.
Schema::create('activity_events', function (Blueprint $table) {
    $table->id();
    $table->foreignId('employee_id')->nullable()->constrained()->nullOnDelete();
    $table->foreignId('camera_id')->constrained()->cascadeOnDelete();
    $table->string('event_type');          // presence | working | phone_use | interaction
    $table->float('start_s');
    $table->float('end_s');
    $table->float('duration_s');
    $table->json('peers')->nullable();     // for interaction events
    $table->string('raw_employee_key')->nullable(); // job_num string as returned, kept even if unmatched
    $table->string('vision_job_id');
    $table->timestamps();
});
```

No new column on `employees` — `job_num` (already unique) doubles as the vision identity key,
just cast to a string when calling `/enroll` and when resolving `employee_key` back to an
`Employee`.

## API contract

**Outbound only** — Hr_SmartPay calls the vision service; the vision service never calls back.

```
# On employee create/update, if `image` present:
POST {VISION_SERVICE_URL}/enroll
{ "name": "1042", "face_images": ["http://hr-app:8080/storage/employees/xyz.jpg"] }

# New: trigger processing for one camera's uploaded video
POST /camera/{id}/process              (Hr_SmartPay, new — role-gated same as other camera routes)
  → resolves camera.video to an internal URL, zone.type to zone_type
  → calls vision-service:
    POST /events/run
    { "video_paths": ["http://hr-app:8080/storage/cameras/abc.mp4"],
      "camera_ids": ["7"],
      "zones": [[{ "label": "desk-area-a", "zone_type": "work_area" }]] }
  → stores { vision_job_id, processing_status: "running" } on the camera row
  → dispatches a queued job that polls:
    GET /events/{job_id}   (existing endpoint)
  → on "done": bulk-inserts activity_events, resolving employee_id via job_num

GET /camera/{id}/events                (Hr_SmartPay, new — simple read/report endpoint)
```

**The one networking gotcha that will bite silently if missed:** `Storage::disk('public')`'s
URL is built from `APP_URL` (`config/filesystems.php`: `rtrim(env('APP_URL', ...), '/').'/storage'`).
In Docker, `APP_URL` is the browser-facing `http://localhost:8080` — **unreachable from inside
the `vision-service` container**, which resolves `localhost` to itself. Any code that builds a
URL to hand to the vision service (for a camera's video, or an employee's photo) must use the
docker-network-internal host (`http://hr-app:8080`, added as `INTERNAL_APP_URL` in this
project's `docker-compose.yml`), not `asset()`/`Storage::url()` directly. This is exactly the
class of bug that works fine on a laptop and silently 400s in a container — worth a code comment
where it's used, not just this ADR.

**Auth:** the vision service still has no authentication. Since Hr_SmartPay now only calls out
(never receives inbound calls from the vision side), the minimum fix is a shared-secret header
Hr_SmartPay sends and the vision service checks — a small addition to
`employee_activity_tracker_2026/service/app.py`, not to this repo. Until that exists, anything
that can reach port 8000 can enroll fake identities or read the gallery — fine on a laptop,
not fine the moment this leaves localhost.

## Consequences

- **Much simpler than both previous drafts:** no orchestrator process, no GPU-box deployment
  question, no inbound auth surface on Hr_SmartPay, no `employee_checkins` table (there's no
  entrance-camera concept in this schema — cameras are all just "footage of a zone," so the
  checkin/analytics split from revision 2 doesn't apply here and has been dropped).
- **Docker packaging needed two real fixes**, not just a schema update:
  1. `php artisan storage:link` was never run — `Storage::disk('public')` uploads (camera
     videos, employee photos) have no public URL at all without it, independent of this
     integration. Added to `entrypoint.sh`.
  2. `INTERNAL_APP_URL` added to `docker-compose.yml` for the server-to-server URL problem
     above. Unused until the `/camera/{id}/process` code exists, but documented now so whoever
     writes that endpoint doesn't rediscover the bug the hard way.
- **Still hard:** none of `/camera/{id}/process`, the polling job, or `activity_events` exist
  yet. This ADR proposes them; nothing here is implemented.
- **Nice free win:** since `employee_zone` already exists, a future optimization is restricting
  identity matching to only employees assigned to a camera's zone (smaller candidate set, fewer
  misidentifications) — the vision service doesn't support a partial-gallery filter today, so
  this is a "worth knowing it's possible," not a current capability.

## Risks / open questions

- Vision service has no auth (see above) — flag as a known gap, not an oversight.
- `zones.type` defaults to `work_area`; nobody has decided yet which existing zones should be
  `common_area` — that's a data decision for whoever seeds/edits zones, not a code problem.
- Single uploaded video per camera means "processing" is inherently one-shot, not continuous.
  If the real deployment needs cameras to be re-processed periodically (new footage replacing
  old), `Camera` needs either versioned videos or a "replace + reprocess" flow — not designed
  here, flagging because "near real-time (minutes)" was the stated goal and a one-shot upload
  model doesn't obviously deliver that without someone re-uploading footage regularly.
- The dead `qr_token` route (see Context) will confuse anyone testing employee-facing profile
  links during a demo — worth a one-line fix independent of this ADR.

## Action items

1. [ ] Migration: `zones.type` (default `work_area`).
2. [ ] Migration: `cameras.vision_job_id`, `cameras.processing_status`, `cameras.processed_at`.
3. [ ] Migration + model: `activity_events`.
4. [ ] Hook `POST /enroll` into `EmployeeController::store`/`update` whenever `image` is present, using `job_num` as `name` and `INTERNAL_APP_URL` for the image URL.
5. [ ] `POST /camera/{id}/process` + queued polling job + `GET /camera/{id}/events`.
6. [ ] Add a shared-secret header check to the vision service (`employee_activity_tracker_2026`, not this repo).
7. [ ] Decide the re-processing story for cameras (versioned uploads vs. replace-and-rerun) if "near real-time" needs to mean more than "processed once shortly after upload."
8. [ ] (Housekeeping, unrelated to this ADR but cheap to fix alongside) Remove or fix the dead `qr_token` lookup in `showProfile()`.
