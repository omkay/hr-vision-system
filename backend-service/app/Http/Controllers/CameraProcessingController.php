<?php

namespace App\Http\Controllers;

use App\Jobs\PollVisionEventsJob;
use App\Models\ActivityEvent;
use App\Models\Camera;
use App\Models\VisionJob;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;

/**
 * Zone-based activity tracking — triggers the vision service's async
 * /events/run pipeline for one or more cameras' uploaded videos, and serves
 * back the persisted results. See INTEGRATION-TODO-multi-photo-enrollment.md
 * section 3. Distinct from CheckinController (daily attendance).
 */
class CameraProcessingController extends Controller
{
    /**
     * Builds the video_paths/camera_ids/zones triple that /events/run
     * expects, for a given set of cameras. Every zone is the full frame —
     * no coordinates, no sub-regions (see the doc for why).
     */
    private function buildEventsPayload($cameras): array
    {
        $baseUrl = rtrim(config('app.internal_url'), '/') . '/storage/';

        $videoPaths = [];
        $cameraIds = [];
        $zones = [];

        foreach ($cameras as $camera) {
            $videoPaths[] = $baseUrl . $camera->video;
            $cameraIds[] = (string) $camera->id;
            $zones[] = [[
                'label' => $camera->zone->name,
                'zone_type' => $camera->zone->zone_type,
            ]];
        }

        return [
            'video_paths' => $videoPaths,
            'camera_ids' => $cameraIds,
            'zones' => $zones,
        ];
    }

    private function submitJob(Request $request, $cameras)
    {
        $missingVideo = $cameras->first(fn ($c) => empty($c->video));
        if ($missingVideo) {
            return response()->json([
                'message' => "الكاميرا '{$missingVideo->name}' لا تحتوي على فيديو مرفوع",
            ], 422);
        }

        $payload = $this->buildEventsPayload($cameras);
        $visionUrl = rtrim(config('services.vision.url'), '/') . '/events/run';

        try {
            $response = Http::timeout(30)->post($visionUrl, $payload);
        } catch (\Throwable $e) {
            Log::error('events/run call failed', ['error' => $e->getMessage()]);

            return response()->json([
                'message' => 'تعذر الاتصال بخدمة الرؤية، حاول مرة أخرى',
            ], 502);
        }

        if ($response->failed()) {
            Log::warning('events/run returned an error', [
                'status' => $response->status(),
                'body' => $response->body(),
            ]);

            return response()->json([
                'message' => 'تعذر بدء المعالجة، حاول مرة أخرى',
            ], 502);
        }

        $body = $response->json();

        $visionJob = VisionJob::create([
            'vision_job_id' => $body['job_id'],
            'status' => $body['status'] ?? 'queued',
            'requested_by' => $request->user()?->id,
        ]);

        $visionJob->cameras()->attach($cameras->pluck('id'));

        PollVisionEventsJob::dispatch($visionJob->id);

        return response()->json([
            'message' => 'تم بدء معالجة الفيديو',
            'job' => [
                'id' => $visionJob->id,
                'vision_job_id' => $visionJob->vision_job_id,
                'status' => $visionJob->status,
                'cameras' => $cameras->pluck('name'),
            ],
        ], 202);
    }

    public function process(Request $request, $id)
    {
        $camera = Camera::with('zone')->findOrFail($id);

        return $this->submitJob($request, collect([$camera]));
    }

    public function processBatch(Request $request)
    {
        $request->validate([
            'camera_ids' => 'required|array|min:1',
            'camera_ids.*' => 'exists:cameras,id',
        ]);

        $cameras = Camera::with('zone')->whereIn('id', $request->camera_ids)->get();

        return $this->submitJob($request, $cameras);
    }

    public function events(Request $request, $id)
    {
        Camera::findOrFail($id);

        $query = ActivityEvent::where('camera_id', $id)->with('employee:id,name,job_num');

        if ($request->filled('employee_id')) {
            $query->where('employee_id', $request->employee_id);
        }

        if ($request->filled('event_type')) {
            $query->where('event_type', $request->event_type);
        }

        if ($request->filled('date')) {
            $query->whereDate('created_at', $request->date);
        }

        $events = $query->latest()->get();

        return response()->json([
            'message' => 'أحداث الكاميرا',
            'events' => $events->map(fn ($e) => [
                'id' => $e->id,
                'employee_id' => $e->employee_id,
                'employee_name' => $e->employee?->name,
                'job_num' => $e->employee?->job_num,
                'event_type' => $e->event_type,
                'start_s' => $e->start_s,
                'end_s' => $e->end_s,
                'duration_s' => $e->duration_s,
                'zone' => $e->zone,
                'zone_type' => $e->zone_type,
                'work_proxy' => $e->work_proxy,
                'peers' => $e->peers,
            ]),
        ], 200);
    }
}
