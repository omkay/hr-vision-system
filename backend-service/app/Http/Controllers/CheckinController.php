<?php

namespace App\Http\Controllers;

use App\Models\ActivityEvent;
use App\Services\CheckinService;
use Illuminate\Http\Request;
use OpenApi\Attributes as OA;

/**
 * Daily attendance checkin — "who was present today" — now unified into the
 * same activity_events "chain of events" that zone/events processing writes
 * to (event_type = 'checkin'), rather than a separate employee_checkins
 * table. This is what lets reporting query one timeline by date / employee /
 * zone instead of joining two tables. See the 2026_08_18 migration for the
 * schema change (camera_id/vision_job_id made nullable — a checkin event has
 * neither: the entrance/kiosk video isn't tied to a registered Camera row,
 * and checkin runs synchronously, not through the async job store zone
 * processing uses).
 *
 * A checkin also seeds that day's body-fingerprint gallery in vision-service
 * (see daily_gallery.py / IdentityFuser.match_reid) — the `session_date`
 * returned here should be passed along to zone/events processing for the
 * same day so cross-camera ReID matching prefers today's actual appearance
 * over the static enrollment-time gallery.
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
    public function __construct(private CheckinService $checkinService)
    {
    }

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
            new OA\Response(
                response: 200,
                description: 'Video processed cleanly but matched nobody — `checkins` is empty '
                    . 'and `message` says so. Not an error: the clip was read, no enrolled '
                    . 'employee was recognised in it. (This used to return 422, which also '
                    . 'aborted /process-sequence before any zone camera ran.)',
            ),
            new OA\Response(response: 502, description: 'Could not reach the vision service.'),
        ],
    )]
    public function store(Request $request)
    {
        $request->validate([
            'video' => 'required|file|mimes:mp4,mov,avi,mkv|max:512000',
        ]);

        $result = $this->checkinService->identifyAndRecordCheckins($request->file('video'));

        return response()->json($result['body'], $result['status']);
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
        $query = ActivityEvent::query()
            ->where('event_type', 'checkin')
            ->with('employee:id,name,job_num');

        if ($request->filled('employee_id')) {
            $query->where('employee_id', $request->employee_id);
        }

        if ($request->filled('date')) {
            $query->whereDate('created_at', $request->date);
        }

        $checkins = $query->latest('created_at')->get();

        return response()->json([
            'message' => 'سجل الحضور',
            'checkins' => $checkins->map(fn ($c) => [
                'id' => $c->id,
                'employee_id' => $c->employee_id,
                'employee_name' => $c->employee->name,
                'job_num' => $c->employee->job_num,
                'date' => $c->created_at->format('Y-m-d'),
                'checked_in_at' => $c->created_at,
                'confidence' => $c->confidence,
                'method' => $c->method,
            ]),
        ], 200);
    }
}
