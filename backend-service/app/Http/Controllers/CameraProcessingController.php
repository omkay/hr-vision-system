<?php

namespace App\Http\Controllers;

use App\Jobs\PollVisionEventsJob;
use App\Models\ActivityEvent;
use App\Models\Camera;
use App\Models\VisionJob;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;
use OpenApi\Attributes as OA;

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
            // vision-service's own default (600 frames @ stride 2 = only the
            // first ~60s of video) silently truncated every one of our zone
            // clips except the shortest — confirmed via a "Working Area" run
            // that returned zero events despite a person clearly being in
            // frame later in the clip. These demo clips run 1-2.5 minutes;
            // 6000 frames @ stride 2 covers ~10 minutes, comfortably more
            // than any of them, without materially slowing down the shorter
            // ones (the pipeline stops at end-of-video regardless).
            'max_frames' => 6000,
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

    #[OA\Post(
        path: '/camera/{id}/process',
        summary: 'Submit one camera\'s video for zone/activity processing',
        description: 'Async — returns immediately with a job id. Poll GET /vision-jobs/{id} for status, then GET /camera/{id}/events once done.',
        tags: ['Processing'],
        security: [['bearerAuth' => []]],
        parameters: [new OA\Parameter(name: 'id', in: 'path', required: true, description: 'Camera ID.', schema: new OA\Schema(type: 'integer'))],
        responses: [
            new OA\Response(response: 202, description: 'Job submitted.'),
            new OA\Response(response: 422, description: 'Camera has no uploaded video.'),
            new OA\Response(response: 502, description: 'Could not reach or start the vision service job.'),
        ],
    )]
    public function process(Request $request, $id)
    {
        $camera = Camera::with('zone')->findOrFail($id);

        return $this->submitJob($request, collect([$camera]));
    }

    /**
     * Polling target for the frontend to show job progress without needing
     * to tail container logs — PollVisionEventsJob already updates this row
     * (queued -> running -> done|error) as it polls the vision service, so
     * this just surfaces that existing state.
     */
    #[OA\Get(
        path: '/vision-jobs/{id}',
        summary: 'Poll a submitted processing job\'s status',
        description: 'status transitions queued -> running -> done|error. On done, fetch results via GET /camera/{id}/events.',
        tags: ['Processing'],
        security: [['bearerAuth' => []]],
        parameters: [new OA\Parameter(name: 'id', in: 'path', required: true, description: 'VisionJob id (the `job.id` returned by /camera/{id}/process or /cameras/process-batch).', schema: new OA\Schema(type: 'integer'))],
        responses: [new OA\Response(response: 200, description: 'Current job status.')],
    )]
    public function jobStatus($id)
    {
        $job = VisionJob::with('cameras:id,name')->findOrFail($id);

        return response()->json([
            'id' => $job->id,
            'vision_job_id' => $job->vision_job_id,
            'status' => $job->status,
            'error_message' => $job->error_message,
            'finished_at' => $job->finished_at,
            'cameras' => $job->cameras->pluck('name'),
        ]);
    }

    #[OA\Post(
        path: '/cameras/process-batch',
        summary: 'Submit multiple cameras\' videos for processing as one job',
        tags: ['Processing'],
        security: [['bearerAuth' => []]],
        requestBody: new OA\RequestBody(
            required: true,
            content: new OA\JsonContent(
                required: ['camera_ids'],
                properties: [
                    new OA\Property(property: 'camera_ids', type: 'array', items: new OA\Items(type: 'integer'), example: [1, 2, 3]),
                ],
            ),
        ),
        responses: [new OA\Response(response: 202, description: 'Job submitted for all cameras.')],
    )]
    public function processBatch(Request $request)
    {
        $request->validate([
            'camera_ids' => 'required|array|min:1',
            'camera_ids.*' => 'exists:cameras,id',
        ]);

        $cameras = Camera::with('zone')->whereIn('id', $request->camera_ids)->get();

        return $this->submitJob($request, $cameras);
    }

    #[OA\Get(
        path: '/camera/{id}/events',
        summary: 'List detected activity events for a camera',
        tags: ['Processing'],
        security: [['bearerAuth' => []]],
        parameters: [
            new OA\Parameter(name: 'id', in: 'path', required: true, description: 'Camera ID.', schema: new OA\Schema(type: 'integer')),
            new OA\Parameter(name: 'employee_id', in: 'query', required: false, schema: new OA\Schema(type: 'integer')),
            new OA\Parameter(name: 'event_type', in: 'query', required: false, schema: new OA\Schema(type: 'string', enum: ['presence', 'working', 'phone_use', 'interaction'])),
            new OA\Parameter(name: 'date', in: 'query', required: false, schema: new OA\Schema(type: 'string', format: 'date')),
        ],
        responses: [new OA\Response(response: 200, description: 'Matching activity events.')],
    )]
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

    #[OA\Get(
        path: '/events',
        summary: 'List detected activity events across every camera',
        description: 'Same data as GET /camera/{id}/events, but not scoped to one camera — '
            . 'use this for a single combined view across every zone/video processed so far.',
        tags: ['Processing'],
        security: [['bearerAuth' => []]],
        parameters: [
            new OA\Parameter(name: 'camera_id', in: 'query', required: false, schema: new OA\Schema(type: 'integer')),
            new OA\Parameter(name: 'employee_id', in: 'query', required: false, schema: new OA\Schema(type: 'integer')),
            new OA\Parameter(name: 'event_type', in: 'query', required: false, schema: new OA\Schema(type: 'string', enum: ['presence', 'working', 'phone_use', 'interaction'])),
            new OA\Parameter(name: 'date', in: 'query', required: false, schema: new OA\Schema(type: 'string', format: 'date')),
        ],
        responses: [new OA\Response(response: 200, description: 'Matching activity events across all cameras.')],
    )]
    public function allEvents(Request $request)
    {
        $query = ActivityEvent::query()->with(['employee:id,name,job_num', 'camera:id,name']);

        if ($request->filled('camera_id')) {
            $query->where('camera_id', $request->camera_id);
        }

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
            'message' => 'كل الأحداث',
            'event_count' => $events->count(),
            'events' => $events->map(fn ($e) => [
                'id' => $e->id,
                'camera_id' => $e->camera_id,
                'camera_name' => $e->camera?->name,
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
