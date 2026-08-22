<?php

namespace App\Services;

use App\Models\ActivityEvent;
use App\Models\Camera;
use App\Models\Employee;
use Illuminate\Http\UploadedFile;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;

/**
 * Shared checkin-video processing logic — extracted out of CheckinController
 * so it can also be called by CameraProcessingController::processSequence(),
 * which runs the checkin video as the first step of a full day's video
 * sequence (checkin, then zone cameras) rather than as a standalone request.
 * Both callers need identical behavior: same dedup rule (one checkin event
 * per employee per day), same ActivityEvent shape, same session_date
 * handoff for the daily body-fingerprint gallery in vision-service.
 */
class CheckinService
{
    /**
     * Uploads $video, runs it through vision-service's /checkin/video-multi,
     * and records one 'checkin' ActivityEvent per newly-identified employee
     * (skipping anyone already checked in today).
     *
     * @return array{ok: bool, status: int, body: array}
     */
    public function identifyAndRecordCheckins(UploadedFile $video): array
    {
        $videoPath = $video->store('checkins', 'public');
        $videoUrl = rtrim(config('app.internal_url'), '/') . '/storage/' . $videoPath;

        return $this->runCheckinVideo($videoUrl);
    }

    /**
     * Same as identifyAndRecordCheckins(), but for a checkin video that's
     * already stored against a persistent Camera record (e.g. a "Checkin
     * Camera" added like any other zone camera) instead of being freshly
     * uploaded in this request. Avoids re-uploading the same file on every
     * /process-sequence call just to point the checkin pipeline at it.
     */
    public function identifyAndRecordCheckinsFromCamera(Camera $camera): array
    {
        if (empty($camera->video)) {
            return [
                'ok' => false,
                'status' => 422,
                'body' => ['message' => "الكاميرا '{$camera->name}' لا تحتوي على فيديو مرفوع"],
            ];
        }

        $videoUrl = rtrim(config('app.internal_url'), '/') . '/storage/' . $camera->video;

        return $this->runCheckinVideo($videoUrl, $camera);
    }

    /**
     * Runs identifyAndRecordCheckinsFromCamera() for every camera in
     * $cameras (e.g. all cameras flagged is_checkin=true — see
     * CameraProcessingController::processSequence()) and merges the results
     * into one combined response, so multiple entrances/checkin points can
     * all seed the same day's fingerprint gallery before any zone camera is
     * processed. Runs sequentially rather than in parallel — the vision
     * service already has no early exit on /checkin/video-multi and this
     * keeps failure isolation simple (one bad camera doesn't abort the rest).
     *
     * @param  \Illuminate\Support\Collection<int, Camera>  $cameras
     * @return array{ok: bool, status: int, body: array}
     */
    public function identifyAndRecordCheckinsFromCameras($cameras): array
    {
        $allCheckins = [];
        $allFingerprinted = [];
        $totalTracks = 0;
        $sessionDate = null;
        $anyOk = false;
        $anyRecognized = false;
        $errors = [];

        foreach ($cameras as $camera) {
            $result = $this->identifyAndRecordCheckinsFromCamera($camera);

            if (! $result['ok']) {
                // Keep going — one entrance camera failing (no video, vision
                // service hiccup, nobody recognized in that specific clip)
                // shouldn't block checkins that other flagged cameras did
                // successfully record.
                $errors[] = [
                    'camera_id' => $camera->id,
                    'camera_name' => $camera->name,
                    'status' => $result['status'],
                    'message' => $result['body']['message'] ?? 'checkin failed',
                ];
                continue;
            }

            $anyOk = true;
            $anyRecognized = $anyRecognized || ($result['recognized'] ?? true);
            $allCheckins = array_merge($allCheckins, $result['body']['checkins'] ?? []);
            $allFingerprinted = array_merge($allFingerprinted, $result['body']['fingerprinted'] ?? []);
            $totalTracks += $result['body']['num_tracks'] ?? 0;
            // All flagged cameras are processed back-to-back "right now", so
            // they should all land on the same calendar day — but take the
            // FIRST successful one as canonical in case this ever runs
            // right at a midnight boundary, rather than letting whichever
            // camera happens to finish last silently decide the date zone
            // cameras get told to match against.
            $sessionDate ??= $result['body']['session_date'];
        }

        if (! $anyOk) {
            return [
                'ok' => false,
                'status' => 422,
                'body' => [
                    'message' => 'تعذرت معالجة أي من كاميرات تسجيل الحضور',
                    'errors' => $errors,
                ],
            ];
        }

        return [
            'ok' => true,
            'status' => 201,
            // False when every entrance clip processed cleanly but matched
            // nobody — a normal, reportable outcome rather than an error, and
            // no longer a reason to skip zone processing (see runCheckinVideo).
            'recognized' => $anyRecognized,
            'body' => [
                'message' => $anyRecognized
                    ? 'تمت معالجة كاميرات تسجيل الحضور'
                    : 'تمت معالجة كاميرات تسجيل الحضور، لكن لم يتم التعرف على أي موظف',
                'checkins' => $allCheckins,
                'num_tracks' => $totalTracks,
                'session_date' => $sessionDate,
                'fingerprinted' => array_values(array_unique($allFingerprinted)),
                'errors' => $errors, // present but empty when every camera succeeded
            ],
        ];
    }

    /**
     * Shared tail end of both entry points above — everything from calling
     * vision-service's /checkin/video-multi onward is identical whether the
     * video came from a fresh upload or an already-stored camera.
     *
     * $camera is the entrance camera this clip belongs to, when there is one
     * (null for a one-off uploaded video). Recorded on the checkin event so
     * that with several entrances flagged is_checkin=true it stays possible
     * to tell WHERE someone checked in, not just that they did.
     */
    private function runCheckinVideo(string $videoUrl, ?Camera $camera = null): array
    {
        $visionUrl = rtrim(config('services.vision.url'), '/') . '/checkin/video-multi';

        try {
            // No early exit on this call (see checkin_video_multi's own
            // docstring) — it reads through the whole clip, running full
            // YOLO detection + tracking + face-matching per sampled frame on
            // CPU-only inference. 900s matches PHP's own raised
            // max_execution_time so neither layer cuts it off first.
            $response = Http::timeout(900)->post($visionUrl, [
                'source' => $videoUrl,
                // vision-service defaults to 600 processed frames — at
                // stride 2 that is only the first ~20-40s of an entrance
                // clip, so anyone who walks in after that is never looked
                // at and simply doesn't get checked in. buildEventsPayload()
                // already raises this for zone cameras; the checkin leg was
                // left on the default, which mattered more here: a missed
                // person loses their whole day's identification, since this
                // is where their body fingerprint comes from.
                'max_frames' => 6000,
                // Per-frame identity decision log for the checkin scan
                // (outputs/checkin_<video>_identity_debug.csv). This step is
                // the root of the whole day's identity chain — it is the only
                // camera where a face is reliably visible, and its output
                // seeds the body fingerprints every other camera matches
                // against. When zone cameras return UNKNOWN, this log says
                // whether the cause is here or downstream. Default is on;
                // see config/services.php 'debug_identity'.
                'debug_identity' => (bool) config('services.vision.debug_identity'),
            ]);
        } catch (\Throwable $e) {
            Log::error('Checkin vision-service call failed', [
                'error' => $e->getMessage(),
            ]);

            return [
                'ok' => false,
                'status' => 502,
                'body' => ['message' => 'تعذر الاتصال بخدمة التعرف، حاول مرة أخرى'],
            ];
        }

        if ($response->failed()) {
            Log::warning('Checkin vision-service call returned an error', [
                'status' => $response->status(),
                'body' => $response->body(),
            ]);

            return [
                'ok' => false,
                'status' => 502,
                'body' => ['message' => 'تعذر التعرف على الفيديو، حاول مرة أخرى'],
            ];
        }

        $result = $response->json();
        $matches = $result['matches'] ?? [];

        // "Nobody was recognised" is NOT a failure of this call — the video
        // was processed fine, it just didn't match anyone. Returning ok:false
        // here used to abort CameraProcessingController::processSequence()
        // before it submitted the zone job at all, so an entrance clip where
        // recognition failed produced no zone processing whatsoever: no
        // presence events, no annotated video, no identity debug log — i.e.
        // nothing to diagnose the recognition failure with, exactly when it
        // was most needed. Zone cameras can still run (identities there fall
        // back to the enrollment gallery / UNKNOWN), so let the caller decide
        // what an empty result means. `recognized` says which it was.
        if (empty($matches)) {
            Log::warning('Checkin video recognised nobody', [
                'camera_id' => $camera?->id,
                'session_date' => $result['session_date'] ?? null,
                'num_tracks' => $result['num_tracks'] ?? 0,
                'frames_processed' => $result['frames_processed'] ?? null,
            ]);

            return [
                'ok' => true,
                'status' => 200,
                'recognized' => false,
                'body' => [
                    'message' => 'لم يتم التعرف على أي موظف في الفيديو',
                    'checkins' => [],
                    'num_tracks' => $result['num_tracks'] ?? 0,
                    'session_date' => $result['session_date'] ?? now()->toDateString(),
                    'fingerprinted' => $result['fingerprinted'] ?? [],
                    'result' => $result,
                ],
            ];
        }

        // vision-service saved each identified employee's daily body
        // fingerprint under this date (defaults to today there too) —
        // callers pass this back as `session_date` to /events/run for zone
        // cameras processed the same day. See daily_gallery.py /
        // IdentityFuser.match_reid.
        $sessionDate = $result['session_date'] ?? now()->toDateString();
        $todayStart = now()->startOfDay();
        $checkins = [];
        // Employees whose daily body fingerprint was actually saved from this
        // clip. Narrower than `matches`: vision-service only lets a
        // face-confirmed track with quality-passing crops seed the day's ReID
        // reference. Anyone identified but NOT in here won't be matchable on
        // the other cameras today (there's no enrollment body bank — we
        // enroll faces only), so it's worth surfacing rather than hiding.
        $fingerprinted = $result['fingerprinted'] ?? [];

        foreach ($matches as $match) {
            $matchedJobNum = $match['employee_id'] ?? null;
            $confidence = $match['confidence'] ?? 0;

            $employee = $matchedJobNum ? Employee::where('job_num', $matchedJobNum)->first() : null;

            if (! $employee) {
                // The gallery has an identity Hr_SmartPay doesn't recognize —
                // the employee record was likely deleted after enrollment.
                // Flag it rather than silently checking in a ghost employee_id,
                // but keep processing the rest of the people in this clip.
                Log::warning('Checkin video matched an unknown job_num', [
                    'matched_job_num' => $matchedJobNum,
                ]);

                $checkins[] = [
                    'job_num' => $matchedJobNum,
                    'status' => 'unrecognized_employee',
                ];
                continue;
            }

            // One checkin event per employee per day — application-level
            // dedup since activity_events has no unique constraint the way
            // the old employee_checkins table did.
            $existing = ActivityEvent::where('employee_id', $employee->id)
                ->where('event_type', 'checkin')
                ->where('created_at', '>=', $todayStart)
                ->first();

            if ($existing) {
                $checkins[] = [
                    'employee_id' => $employee->id,
                    'name' => $employee->name,
                    'status' => 'already_checked_in',
                    'checked_in_at' => $existing->created_at,
                ];
                continue;
            }

            // checkin_video_multi reports `confidence` as a FACE cosine
            // similarity and leaves it at 0 when the track was identified by
            // body matching alone, so this is now derived rather than assumed
            // 'face' as it was before. It matters for attendance: a
            // body-only checkin is a weaker claim about who walked in, and
            // recording it as a face match hid that entirely.
            $method = $confidence > 0 ? 'face' : 'reid';

            $event = ActivityEvent::create([
                // The entrance camera this checkin came from, when the clip
                // belongs to one. Previously always null, which made every
                // checkin unattributable once more than one camera was
                // flagged is_checkin=true — and invisible to
                // GET /camera/{id}/events, which filters on camera_id.
                'camera_id' => $camera?->id,
                'vision_job_id' => null,
                'employee_id' => $employee->id,
                'confidence' => $confidence,
                'method' => $method,
                'event_type' => 'checkin',
                'start_s' => 0,
                'end_s' => 0,
                'duration_s' => 0,
                'zone' => $camera?->zone?->name ?? 'entrance',
            ]);

            $checkins[] = [
                'employee_id' => $employee->id,
                'name' => $employee->name,
                'job_num' => $employee->job_num,
                'status' => 'checked_in',
                'checked_in_at' => $event->created_at,
                'confidence' => $event->confidence,
                'method' => $method,
                'camera_id' => $camera?->id,
                // False here means: identified, but no body fingerprint was
                // stored for today — so this employee will NOT be matchable
                // on the zone cameras today. The usual cause is that every
                // usable crop of them failed the quality gate.
                // Cast to string before the strict comparison: job_num is cast
                // to an integer on the Employee model, while vision-service
                // returns gallery keys as strings (they're directory names).
                // in_array(5, ['4','5'], true) is false, which reported
                // "not fingerprinted" for employees that were.
                'fingerprinted' => in_array((string) $employee->job_num, $fingerprinted, true),
            ];
        }

        return [
            'ok' => true,
            'status' => 201,
            'recognized' => true,
            'body' => [
                'message' => 'تمت معالجة الفيديو',
                'checkins' => $checkins,
                'num_tracks' => $result['num_tracks'] ?? count($matches),
                'session_date' => $sessionDate,
                'fingerprinted' => $fingerprinted,
            ],
        ];
    }
}
