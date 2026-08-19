<?php

namespace App\Http\Controllers;

use App\Jobs\PollVisionEventsJob;
use App\Models\ActivityEvent;
use App\Models\Camera;
use App\Models\VisionJob;
use App\Services\CheckinService;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;
use OpenApi\Attributes as OA;

/**
 * Zone-based activity tracking — triggers the vision service's async
 * /events/run pipeline for one or more cameras' uploaded videos, and serves
 * back the persisted results. See INTEGRATION-TODO-multi-photo-enrollment.md
 * section 3. Distinct from CheckinController (daily attendance) — except
 * for processSequence() below, which deliberately chains the two: the
 * checkin video seeds that day's body-fingerprint gallery in vision-service,
 * then the zone cameras are processed against that same day so cross-camera
 * ReID matching benefits from it. See daily_gallery.py in vision-service.
 */
class CameraProcessingController extends Controller
{
    public function __construct(private CheckinService $checkinService)
    {
    }

    /**
     * Builds the video_paths/camera_ids/zones triple that /events/run
     * expects, for a given set of cameras. Every zone is the full frame —
     * no coordinates, no sub-regions (see the doc for why).
     *
     * $sessionDate: passed through as /events/run's `session_date` — which
     * day's daily body-fingerprint gallery vision-service should prefer
     * during ReID matching (see daily_gallery.py). Null means "today", the
     * same default vision-service itself applies.
     */
    private function buildEventsPayload(
        $cameras,
        ?string $sessionDate = null,
        bool $writeVideo = false,
        ?int $annotateStride = null,
    ): array {
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

        $payload = [
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
            'write_video' => $writeVideo,
        ];

        if ($sessionDate !== null) {
            $payload['session_date'] = $sessionDate;
        }

        if ($annotateStride !== null) {
            $payload['annotate_stride'] = $annotateStride;
        }

        return $payload;
    }

    private function submitJob(
        Request $request,
        $cameras,
        ?string $sessionDate = null,
        bool $writeVideo = false,
        ?int $annotateStride = null,
    ) {
        $missingVideo = $cameras->first(fn ($c) => empty($c->video));
        if ($missingVideo) {
            return response()->json([
                'message' => "الكاميرا '{$missingVideo->name}' لا تحتوي على فيديو مرفوع",
            ], 422);
        }

        $payload = $this->buildEventsPayload($cameras, $sessionDate, $writeVideo, $annotateStride);
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
        parameters: [
            new OA\Parameter(name: 'id', in: 'path', required: true, description: 'Camera ID.', schema: new OA\Schema(type: 'integer')),
            new OA\Parameter(name: 'session_date', in: 'query', required: false, description: 'Which day\'s daily body-fingerprint gallery to prefer during ReID matching. Defaults to today.', schema: new OA\Schema(type: 'string', format: 'date')),
            new OA\Parameter(name: 'write_video', in: 'query', required: false, description: 'Generate an annotated debug video (boxes/zones/labels drawn in) — see `annotated_videos` once GET /vision-jobs/{id} reports done.', schema: new OA\Schema(type: 'boolean')),
            new OA\Parameter(name: 'annotate_stride', in: 'query', required: false, description: 'Only relevant when write_video=true — write 1 out of every N processed frames to keep the file small. Defaults to 5.', schema: new OA\Schema(type: 'integer')),
        ],
        responses: [
            new OA\Response(response: 202, description: 'Job submitted.'),
            new OA\Response(response: 422, description: 'Camera has no uploaded video.'),
            new OA\Response(response: 502, description: 'Could not reach or start the vision service job.'),
        ],
    )]
    public function process(Request $request, $id)
    {
        $request->validate([
            'session_date' => 'nullable|date',
            'write_video' => 'nullable|boolean',
            'annotate_stride' => 'nullable|integer|min:1',
        ]);

        $camera = Camera::with('zone')->findOrFail($id);

        return $this->submitJob(
            $request,
            collect([$camera]),
            $request->input('session_date'),
            $request->boolean('write_video'),
            $request->input('annotate_stride'),
        );
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
            'annotated_videos' => $this->decodeAnnotatedVideos($job),
        ]);
    }

    /**
     * annotated_videos (per-camera debug videos with boxes/zones drawn in —
     * only present if write_video was requested) only survive inside the
     * raw_result JSON blob PollVisionEventsJob stores once the job is done;
     * extract + resolve them to browser-fetchable URLs here rather than
     * making every caller (jobStatus, index) do that decoding themselves.
     */
    private function decodeAnnotatedVideos(VisionJob $job): array
    {
        $annotatedVideos = [];
        if ($job->raw_result) {
            $result = json_decode($job->raw_result, true) ?? [];
            $publicBase = rtrim(config('services.vision.public_url'), '/');
            foreach (($result['annotated_videos'] ?? []) as $video) {
                $cameraId = (int) ($video['camera_id'] ?? 0);
                $camera = $job->cameras->firstWhere('id', $cameraId);
                $annotatedVideos[] = [
                    'camera_id' => $cameraId,
                    'camera_name' => $camera->name ?? null,
                    'url' => $publicBase . ($video['path'] ?? ''),
                ];
            }
        }

        return $annotatedVideos;
    }

    #[OA\Get(
        path: '/vision-jobs',
        summary: 'List past processing jobs (with their annotated videos), newest first',
        description: 'Paginated list of vision jobs, optionally filtered by camera/status/date — '
            . 'used to build a video-review page without needing to already know a job id.',
        tags: ['Processing'],
        security: [['bearerAuth' => []]],
        parameters: [
            new OA\Parameter(name: 'camera_id', in: 'query', required: false, description: 'Only jobs that include this camera.', schema: new OA\Schema(type: 'integer')),
            new OA\Parameter(name: 'status', in: 'query', required: false, schema: new OA\Schema(type: 'string', enum: ['queued', 'running', 'done', 'error'])),
            new OA\Parameter(name: 'date_from', in: 'query', required: false, schema: new OA\Schema(type: 'string', format: 'date')),
            new OA\Parameter(name: 'date_to', in: 'query', required: false, schema: new OA\Schema(type: 'string', format: 'date')),
            new OA\Parameter(name: 'per_page', in: 'query', required: false, schema: new OA\Schema(type: 'integer', default: 15)),
            new OA\Parameter(name: 'page', in: 'query', required: false, schema: new OA\Schema(type: 'integer', default: 1)),
        ],
        responses: [new OA\Response(response: 200, description: 'Paginated jobs, each with resolved annotated_videos.')],
    )]
    public function index(Request $request)
    {
        $query = VisionJob::with('cameras:id,name')->latest();

        if ($request->filled('camera_id')) {
            $query->whereHas('cameras', fn ($q) => $q->where('cameras.id', $request->camera_id));
        }

        if ($request->filled('status')) {
            $query->where('status', $request->status);
        }

        if ($request->filled('date_from')) {
            $query->whereDate('created_at', '>=', $request->date_from);
        }

        if ($request->filled('date_to')) {
            $query->whereDate('created_at', '<=', $request->date_to);
        }

        $perPage = (int) $request->input('per_page', 15);
        $jobs = $query->paginate($perPage > 0 ? $perPage : 15);

        return response()->json([
            'message' => 'سجل المهام',
            'current_page' => $jobs->currentPage(),
            'last_page' => $jobs->lastPage(),
            'total' => $jobs->total(),
            'jobs' => collect($jobs->items())->map(fn (VisionJob $job) => [
                'id' => $job->id,
                'vision_job_id' => $job->vision_job_id,
                'status' => $job->status,
                'error_message' => $job->error_message,
                'created_at' => $job->created_at,
                'finished_at' => $job->finished_at,
                'cameras' => $job->cameras->pluck('name'),
                'annotated_videos' => $this->decodeAnnotatedVideos($job),
            ]),
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
                    new OA\Property(property: 'session_date', type: 'string', format: 'date', description: 'Which day\'s daily body-fingerprint gallery to prefer during ReID matching. Defaults to today.'),
                    new OA\Property(property: 'write_video', type: 'boolean', description: 'Generate an annotated debug video per camera (boxes/zones/labels drawn in) — see the `annotated_videos` field once GET /vision-jobs/{id} reports done.'),
                    new OA\Property(property: 'annotate_stride', type: 'integer', description: 'Only relevant when write_video=true — write 1 out of every N processed frames to keep the file small. Defaults to 5.'),
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
            'session_date' => 'nullable|date',
            'write_video' => 'nullable|boolean',
            'annotate_stride' => 'nullable|integer|min:1',
        ]);

        $cameras = Camera::with('zone')->whereIn('id', $request->camera_ids)->get();

        return $this->submitJob(
            $request,
            $cameras,
            $request->input('session_date'),
            $request->boolean('write_video'),
            $request->input('annotate_stride'),
        );
    }

    #[OA\Post(
        path: '/process-sequence',
        summary: "Process a day's video sequence: checkin first, then zone cameras",
        description: "Orchestrates the full daily flow in one call: runs the checkin video "
            . 'through face/ReID identification — which also seeds that day\'s body-fingerprint '
            . 'gallery in vision-service — then submits the given zone cameras for /events/run '
            . 'processing using that same session_date, so cross-camera ReID matching prefers '
            . "today's fresh appearance over the static enrollment gallery. See daily_gallery.py "
            . '/ IdentityFuser.match_reid in vision-service. The checkin video can be supplied '
            . 'either as a fresh upload (`checkin_video`) or by pointing at an already-stored '
            . 'camera (`checkin_camera_id`) — e.g. a persistent "Checkin Camera" added the same '
            . 'way as any other zone camera — so it doesn\'t need to be re-uploaded every call.',
        tags: ['Processing'],
        security: [['bearerAuth' => []]],
        requestBody: new OA\RequestBody(
            required: true,
            content: new OA\MediaType(
                mediaType: 'multipart/form-data',
                schema: new OA\Schema(required: ['camera_ids'], properties: [
                    new OA\Property(property: 'checkin_video', type: 'string', format: 'binary', description: 'mp4/mov/avi/mkv, up to 500MB. Required unless checkin_camera_id is given.'),
                    new OA\Property(property: 'checkin_camera_id', type: 'integer', description: 'Use an already-stored camera\'s video for the checkin step instead of uploading one. Required unless checkin_video is given.'),
                    new OA\Property(property: 'camera_ids', type: 'array', items: new OA\Items(type: 'integer'), example: [1, 2, 3]),
                    new OA\Property(property: 'write_video', type: 'boolean', description: 'Generate an annotated debug video per zone camera (boxes/zones/labels drawn in) — see `annotated_videos` once GET /vision-jobs/{id} reports done.'),
                    new OA\Property(property: 'annotate_stride', type: 'integer', description: 'Only relevant when write_video=true — write 1 out of every N processed frames to keep the file small. Defaults to 5.'),
                ]),
            ),
        ),
        responses: [
            new OA\Response(response: 202, description: 'Checkin processed and zone job submitted — see `checkin` and `job` in the response.'),
            new OA\Response(response: 422, description: 'No employee recognized in the checkin video, or invalid camera_ids.'),
            new OA\Response(response: 502, description: 'Could not reach the vision service.'),
        ],
    )]
    public function processSequence(Request $request)
    {
        $request->validate([
            'checkin_video' => 'required_without:checkin_camera_id|nullable|file|mimes:mp4,mov,avi,mkv|max:512000',
            'checkin_camera_id' => 'required_without:checkin_video|nullable|integer|exists:cameras,id',
            'camera_ids' => 'required|array|min:1',
            'camera_ids.*' => 'exists:cameras,id',
            'write_video' => 'nullable|boolean',
            'annotate_stride' => 'nullable|integer|min:1',
        ]);

        if ($request->filled('checkin_camera_id')) {
            $checkinCamera = Camera::findOrFail($request->input('checkin_camera_id'));
            $checkinResult = $this->checkinService->identifyAndRecordCheckinsFromCamera($checkinCamera);
        } else {
            $checkinResult = $this->checkinService->identifyAndRecordCheckins($request->file('checkin_video'));
        }

        if (! $checkinResult['ok']) {
            return response()->json($checkinResult['body'], $checkinResult['status']);
        }

        $sessionDate = $checkinResult['body']['session_date'];
        $cameras = Camera::with('zone')->whereIn('id', $request->camera_ids)->get();

        $jobResponse = $this->submitJob(
            $request,
            $cameras,
            $sessionDate,
            $request->boolean('write_video'),
            $request->input('annotate_stride'),
        );

        // submitJob() returns a JsonResponse on its own (used standalone by
        // process()/processBatch()) — merge the checkin summary into it here
        // rather than dropping it, since both halves matter to this caller.
        $jobData = $jobResponse->getData(true);
        $jobData['checkin'] = $checkinResult['body'];

        return response()->json($jobData, $jobResponse->getStatusCode());
    }

    #[OA\Get(
        path: '/camera/{id}/events',
        summary: 'List detected activity events for a camera',
        tags: ['Processing'],
        security: [['bearerAuth' => []]],
        parameters: [
            new OA\Parameter(name: 'id', in: 'path', required: true, description: 'Camera ID.', schema: new OA\Schema(type: 'integer')),
            new OA\Parameter(name: 'employee_id', in: 'query', required: false, schema: new OA\Schema(type: 'integer')),
            new OA\Parameter(name: 'event_type', in: 'query', required: false, schema: new OA\Schema(type: 'string', enum: ['presence', 'working', 'phone_use', 'interaction', 'checkin'])),
            new OA\Parameter(name: 'zone', in: 'query', required: false, schema: new OA\Schema(type: 'string')),
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

        if ($request->filled('zone')) {
            $query->where('zone', $request->zone);
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
                'confidence' => $e->confidence,
                'method' => $e->method,
                'start_s' => $e->start_s,
                'end_s' => $e->end_s,
                'duration_s' => $e->duration_s,
                'zone' => $e->zone,
                'zone_type' => $e->zone_type,
                'work_proxy' => $e->work_proxy,
                'peers' => $e->peers,
                'created_at' => $e->created_at,
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
            new OA\Parameter(name: 'event_type', in: 'query', required: false, schema: new OA\Schema(type: 'string', enum: ['presence', 'working', 'phone_use', 'interaction', 'checkin'])),
            new OA\Parameter(name: 'zone', in: 'query', required: false, schema: new OA\Schema(type: 'string')),
            new OA\Parameter(name: 'date', in: 'query', required: false, description: 'Exact day. Ignored if date_from/date_to are given.', schema: new OA\Schema(type: 'string', format: 'date')),
            new OA\Parameter(name: 'date_from', in: 'query', required: false, description: 'Range start (inclusive). Lets callers (e.g. the dashboard) aggregate over more than one day.', schema: new OA\Schema(type: 'string', format: 'date')),
            new OA\Parameter(name: 'date_to', in: 'query', required: false, description: 'Range end (inclusive).', schema: new OA\Schema(type: 'string', format: 'date')),
            new OA\Parameter(name: 'order', in: 'query', required: false, description: "'asc' for chronological (a per-employee 'chain of events'), 'desc' (default) for newest first.", schema: new OA\Schema(type: 'string', enum: ['asc', 'desc'])),
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

        if ($request->filled('zone')) {
            $query->where('zone', $request->zone);
        }

        // date_from/date_to (a range) take precedence over the older single
        // `date` param — added so callers that need to aggregate over more
        // than one day (e.g. the dashboard's productivity charts) don't have
        // to issue one request per day. `date` alone still works exactly as
        // before for anyone not passing a range.
        if ($request->filled('date_from') || $request->filled('date_to')) {
            if ($request->filled('date_from')) {
                $query->whereDate('created_at', '>=', $request->date_from);
            }
            if ($request->filled('date_to')) {
                $query->whereDate('created_at', '<=', $request->date_to);
            }
        } elseif ($request->filled('date')) {
            $query->whereDate('created_at', $request->date);
        }

        $order = $request->input('order') === 'asc' ? 'asc' : 'desc';
        $events = $query->orderBy('created_at', $order)->get();

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
                'confidence' => $e->confidence,
                'method' => $e->method,
                'start_s' => $e->start_s,
                'end_s' => $e->end_s,
                'duration_s' => $e->duration_s,
                'zone' => $e->zone,
                'zone_type' => $e->zone_type,
                'work_proxy' => $e->work_proxy,
                'peers' => $e->peers,
                'created_at' => $e->created_at,
            ]),
        ], 200);
    }
}
