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

        return $this->runCheckinVideo($videoUrl);
    }

    /**
     * Shared tail end of both entry points above — everything from calling
     * vision-service's /checkin/video-multi onward is identical whether the
     * video came from a fresh upload or an already-stored camera.
     */
    private function runCheckinVideo(string $videoUrl): array
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

        if (empty($matches)) {
            return [
                'ok' => false,
                'status' => 422,
                'body' => [
                    'message' => 'لم يتم التعرف على أي موظف في الفيديو',
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

            $event = ActivityEvent::create([
                'camera_id' => null,
                'vision_job_id' => null,
                'employee_id' => $employee->id,
                'confidence' => $confidence,
                // checkin_video_multi doesn't currently distinguish which
                // signal (face vs. reid) committed the identity — see
                // IdentityFuser.update — so this is a known simplification,
                // same as the old employee_checkins write.
                'method' => 'face',
                'event_type' => 'checkin',
                'start_s' => 0,
                'end_s' => 0,
                'duration_s' => 0,
                'zone' => 'entrance',
            ]);

            $checkins[] = [
                'employee_id' => $employee->id,
                'name' => $employee->name,
                'status' => 'checked_in',
                'checked_in_at' => $event->created_at,
                'confidence' => $event->confidence,
            ];
        }

        return [
            'ok' => true,
            'status' => 201,
            'body' => [
                'message' => 'تمت معالجة الفيديو',
                'checkins' => $checkins,
                'num_tracks' => $result['num_tracks'] ?? count($matches),
                'session_date' => $sessionDate,
            ],
        ];
    }
}
