<?php

namespace App\Http\Controllers;

use App\Models\Employee;
use App\Models\EmployeeCheckin;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;
use OpenApi\Attributes as OA;

/**
 * Daily attendance checkin — "who was present today" — distinct from the
 * (separate) zone-based activity tracking feature.
 * See INTEGRATION-TODO-multi-photo-enrollment.md section 2 for the design.
 *
 * Identity is discovered FROM the footage, not supplied by the caller —
 * employees have no login accounts of their own in this app, so a kiosk or
 * entrance camera has no other way to know who's checking in.
 *
 * Takes a VIDEO clip (not a single photo) and identifies EVERY distinct
 * employee that appears in it via the vision service's /checkin/video-multi
 * — a real entrance/lobby camera clip commonly has more than one person
 * walk through in the same window (this is exactly the scenario the
 * checkin-video demo surfaced: both Hasan and Majd in one clip), and a
 * single-photo/single-answer endpoint could only ever check one of them in.
 * One request can therefore produce zero, one, or several checkin records —
 * see `checkins` in the response, one entry per identified person.
 */
class CheckinController extends Controller
{
    #[OA\Post(
        path: '/checkin',
        summary: 'Kiosk/entrance video checkin — identifies every distinct employee in the clip',
        description: 'Identity is determined from the footage, not supplied by the caller. '
            . 'A single video clip may contain more than one employee (e.g. several people passing '
            . 'an entrance camera together) — every distinct person recognized gets their own entry '
            . 'in the `checkins` array of the response, each with its own status.',
        tags: ['Checkin'],
        security: [['bearerAuth' => []]],
        requestBody: new OA\RequestBody(
            required: true,
            content: new OA\MediaType(
                mediaType: 'multipart/form-data',
                schema: new OA\Schema(required: ['video'], properties: [
                    new OA\Property(property: 'video', type: 'string', format: 'binary', description: 'mp4/mov/avi/mkv, up to 500MB.'),
                ]),
            ),
        ),
        responses: [
            new OA\Response(
                response: 201,
                description: 'Video processed — see `checkins` for a per-employee status '
                    . '(`checked_in`, `already_checked_in`, or `unrecognized_employee`).',
            ),
            new OA\Response(response: 422, description: 'No employee recognized anywhere in the clip.'),
            new OA\Response(response: 502, description: 'Could not reach the vision service.'),
        ],
    )]
    public function store(Request $request)
    {
        $request->validate([
            'video' => 'required|file|mimes:mp4,mov,avi,mkv|max:512000',
        ]);

        $videoPath = $request->file('video')->store('checkins', 'public');
        $videoUrl = rtrim(config('app.internal_url'), '/') . '/storage/' . $videoPath;
        $visionUrl = rtrim(config('services.vision.url'), '/') . '/checkin/video-multi';

        try {
            // No early exit on this endpoint (see checkin_video_multi's own
            // docstring) — it has to read through the whole clip, running full
            // YOLO detection + tracking + face-matching per sampled frame on
            // CPU-only inference. 120s wasn't enough for a multi-minute 4K
            // clip (confirmed via a real 502 timeout) — 900s matches PHP's own
            // raised max_execution_time so neither layer cuts it off first.
            $response = Http::timeout(900)->post($visionUrl, [
                'source' => $videoUrl,
            ]);
        } catch (\Throwable $e) {
            Log::error('Checkin vision-service call failed', [
                'error' => $e->getMessage(),
            ]);

            return response()->json([
                'message' => 'تعذر الاتصال بخدمة التعرف، حاول مرة أخرى',
            ], 502);
        }

        if ($response->failed()) {
            Log::warning('Checkin vision-service call returned an error', [
                'status' => $response->status(),
                'body' => $response->body(),
            ]);

            return response()->json([
                'message' => 'تعذر التعرف على الفيديو، حاول مرة أخرى',
            ], 502);
        }

        $result = $response->json();
        $matches = $result['matches'] ?? [];

        if (empty($matches)) {
            return response()->json([
                'message' => 'لم يتم التعرف على أي موظف في الفيديو',
                'result' => $result,
            ], 422);
        }

        $today = now()->toDateString();
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

            $existing = EmployeeCheckin::where('employee_id', $employee->id)
                ->where('date', $today)
                ->first();

            if ($existing) {
                $checkins[] = [
                    'employee_id' => $employee->id,
                    'name' => $employee->name,
                    'status' => 'already_checked_in',
                    'checked_in_at' => $existing->checked_in_at,
                ];
                continue;
            }

            $checkin = EmployeeCheckin::create([
                'employee_id' => $employee->id,
                'date' => $today,
                'checked_in_at' => now(),
                'confidence' => $confidence,
                'method' => 'face',
                'photo_path' => $videoPath,
            ]);

            $checkins[] = [
                'employee_id' => $employee->id,
                'name' => $employee->name,
                'status' => 'checked_in',
                'checked_in_at' => $checkin->checked_in_at,
                'confidence' => $checkin->confidence,
            ];
        }

        return response()->json([
            'message' => 'تمت معالجة الفيديو',
            'checkins' => $checkins,
            'num_tracks' => $result['num_tracks'] ?? count($matches),
        ], 201);
    }

    #[OA\Get(
        path: '/checkins',
        summary: 'List recorded checkins',
        tags: ['Checkin'],
        security: [['bearerAuth' => []]],
        parameters: [
            new OA\Parameter(name: 'employee_id', in: 'query', required: false, schema: new OA\Schema(type: 'integer')),
            new OA\Parameter(name: 'date', in: 'query', required: false, schema: new OA\Schema(type: 'string', format: 'date')),
        ],
        responses: [new OA\Response(response: 200, description: 'Matching checkins.')],
    )]
    public function index(Request $request)
    {
        $query = EmployeeCheckin::query()->with('employee:id,name,job_num');

        if ($request->filled('employee_id')) {
            $query->where('employee_id', $request->employee_id);
        }

        if ($request->filled('date')) {
            $query->where('date', $request->date);
        }

        $checkins = $query->latest('checked_in_at')->get();

        return response()->json([
            'message' => 'سجل الحضور',
            'checkins' => $checkins->map(fn ($c) => [
                'id' => $c->id,
                'employee_id' => $c->employee_id,
                'employee_name' => $c->employee->name,
                'job_num' => $c->employee->job_num,
                'date' => $c->date->format('Y-m-d'),
                'checked_in_at' => $c->checked_in_at,
                'confidence' => $c->confidence,
                'method' => $c->method,
            ]),
        ], 200);
    }
}
