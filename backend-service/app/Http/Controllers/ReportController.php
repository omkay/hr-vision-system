<?php

namespace App\Http\Controllers;

use App\Models\ActivityEvent;
use App\Models\Employee;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use OpenApi\Attributes as OA;

/**
 * Aggregated reporting over the unified activity_events "chain of events"
 * (zone presence/working/phone_use/interaction, plus checkin — see
 * CheckinController). Groups by any combination of date / employee / zone
 * and returns per-group totals, rather than a raw event list (that's what
 * GET /events is for — see CameraProcessingController::allEvents()).
 */
class ReportController extends Controller
{
    private const VALID_GROUPS = ['date', 'employee', 'zone'];

    #[OA\Get(
        path: '/reports/summary',
        summary: 'Aggregated event counts/durations by date, employee, and/or zone',
        description: 'Groups activity_events (including checkin) by any combination of '
            . "'date', 'employee', 'zone' (default: date,employee) and returns event_count "
            . 'and total_duration_s per group. Filter further with employee_id, zone, '
            . 'event_type, date_from, date_to.',
        tags: ['Reports'],
        security: [['bearerAuth' => []]],
        parameters: [
            new OA\Parameter(name: 'group_by', in: 'query', required: false, description: "Comma-separated subset of 'date', 'employee', 'zone'. Defaults to 'date,employee'.", schema: new OA\Schema(type: 'string', example: 'date,employee,zone')),
            new OA\Parameter(name: 'employee_id', in: 'query', required: false, schema: new OA\Schema(type: 'integer')),
            new OA\Parameter(name: 'zone', in: 'query', required: false, schema: new OA\Schema(type: 'string')),
            new OA\Parameter(name: 'event_type', in: 'query', required: false, schema: new OA\Schema(type: 'string', enum: ['presence', 'working', 'phone_use', 'interaction', 'checkin', 'checkout'])),
            new OA\Parameter(name: 'date_from', in: 'query', required: false, schema: new OA\Schema(type: 'string', format: 'date')),
            new OA\Parameter(name: 'date_to', in: 'query', required: false, schema: new OA\Schema(type: 'string', format: 'date')),
            new OA\Parameter(name: 'identified_only', in: 'query', required: false, description: 'Only events attributed to a known employee (employee_id IS NOT NULL). Use for any per-employee metric — unidentified events have no employee to attribute time to and would inflate totals and averages.', schema: new OA\Schema(type: 'boolean')),
            new OA\Parameter(name: 'unidentified_only', in: 'query', required: false, description: 'Only events the vision pipeline could not attribute (employee_id IS NULL, i.e. UNKNOWN). Use to report on unidentified activity separately — e.g. an unrecognised person in a work area. Ignored if identified_only is also set.', schema: new OA\Schema(type: 'boolean')),
        ],
        responses: [new OA\Response(response: 200, description: 'Aggregated report groups.')],
    )]
    public function summary(Request $request)
    {
        $requestedGroups = array_filter(explode(',', $request->input('group_by', 'date,employee')));
        $groups = array_values(array_intersect(self::VALID_GROUPS, $requestedGroups));
        if (empty($groups)) {
            $groups = ['date', 'employee'];
        }

        $query = ActivityEvent::query();

        if ($request->filled('employee_id')) {
            $query->where('employee_id', $request->employee_id);
        }
        if ($request->filled('zone')) {
            $query->where('zone', $request->zone);
        }
        if ($request->filled('event_type')) {
            $query->where('event_type', $request->event_type);
        }
        if ($request->filled('date_from')) {
            $query->whereDate('created_at', '>=', $request->date_from);
        }
        if ($request->filled('date_to')) {
            $query->whereDate('created_at', '<=', $request->date_to);
        }

        // Identity filter. Unidentified events (employee_id IS NULL — the
        // vision pipeline returned UNKNOWN) must not be mixed into
        // per-employee productivity metrics: they have no employee to
        // attribute time to, they inflate totals and averages, and they
        // surface in charts as a "#null" bucket. But they aren't noise
        // either — an unidentified person in a work area is worth reporting
        // on its own — so they're filterable in both directions rather than
        // dropped outright.
        if ($request->boolean('identified_only')) {
            $query->whereNotNull('employee_id');
        } elseif ($request->boolean('unidentified_only')) {
            $query->whereNull('employee_id');
        }

        $selects = [];
        $groupColumns = [];
        if (in_array('date', $groups, true)) {
            $selects[] = DB::raw('DATE(created_at) as report_date');
            $groupColumns[] = DB::raw('DATE(created_at)');
        }
        if (in_array('employee', $groups, true)) {
            $selects[] = 'employee_id';
            $groupColumns[] = 'employee_id';
        }
        if (in_array('zone', $groups, true)) {
            $selects[] = 'zone';
            $groupColumns[] = 'zone';
        }
        $selects[] = DB::raw('COUNT(*) as event_count');
        $selects[] = DB::raw('SUM(duration_s) as total_duration_s');

        $rows = $query->select($selects)->groupBy($groupColumns)->get();

        // Resolve employee names in one query rather than N+1 per row.
        $employeeIds = $rows->pluck('employee_id')->filter()->unique();
        $employees = Employee::whereIn('id', $employeeIds)->get(['id', 'name', 'job_num'])->keyBy('id');

        return response()->json([
            'message' => 'تقرير الأحداث',
            'group_by' => $groups,
            'groups' => $rows->map(function ($row) use ($groups, $employees) {
                $out = [];
                if (in_array('date', $groups, true)) {
                    $out['date'] = $row->report_date;
                }
                if (in_array('employee', $groups, true)) {
                    $emp = $employees->get($row->employee_id);
                    $out['employee_id'] = $row->employee_id;
                    $out['employee_name'] = $emp?->name;
                    $out['job_num'] = $emp?->job_num;
                }
                if (in_array('zone', $groups, true)) {
                    $out['zone'] = $row->zone;
                }
                $out['event_count'] = (int) $row->event_count;
                $out['total_duration_s'] = round((float) $row->total_duration_s, 2);

                return $out;
            }),
        ], 200);
    }
}
