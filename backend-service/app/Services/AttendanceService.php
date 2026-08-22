<?php

namespace App\Services;

use App\Models\ActivityEvent;
use App\Models\Employee;
use Carbon\Carbon;
use Illuminate\Support\Facades\Log;

/**
 * Daily attendance: who arrived, who left, and the manual corrections HR
 * needs when recognition gets it wrong.
 *
 * Both halves matter. The cameras produce 'checkin' events automatically (see
 * CheckinService) and this derives the matching 'checkout'. But recognition
 * fails on real people for reasons no threshold fixes — a facial injury, a
 * significant change in appearance — and attendance feeds payroll, so a human
 * must be able to set, correct, or remove any of it. Manual entries live in
 * the same activity_events table as the automatic ones so every report picks
 * them up without special-casing; `method = 'manual'` and `recorded_by` are
 * what distinguish "the system saw this" from "a person asserted this".
 */
class AttendanceService
{
    /**
     * How long after an employee was last seen we consider them departed.
     *
     * A deliberately simple rule, and worth being honest about why: none of
     * the current cameras can distinguish "walking out of the building" from
     * "walking past the door", so a genuine exit detection isn't available.
     * The last sighting plus a grace period is a defensible approximation —
     * an employee who leaves is, by definition, not seen again afterwards.
     * The grace period exists because the last sighting is a lower bound:
     * they were still on site at that moment, and typically leave shortly
     * after passing the last camera.
     *
     * The consequence to keep in mind: for someone who stops being detected
     * mid-afternoon (sat still in an uncovered spot, or a recognition gap)
     * this reports an early departure. HR overrides it by hand, which is
     * exactly what the manual path is for.
     */
    public const CHECKOUT_GRACE_MINUTES = 10;

    /**
     * Derive missing checkouts for *date* (Y-m-d), one per employee who has a
     * checkin that day. Idempotent: employees who already have a checkout —
     * automatic or manual — are left alone, so this is safe to re-run after
     * every processing job.
     *
     * @return array{created: int, skipped_existing: int, skipped_no_sighting: int, date: string}
     */
    public function deriveCheckouts(?string $date = null): array
    {
        $date = $date ?: now()->toDateString();
        $dayStart = Carbon::parse($date)->startOfDay();
        $dayEnd = Carbon::parse($date)->endOfDay();

        $checkins = ActivityEvent::whereNotNull('employee_id')
            ->where('event_type', 'checkin')
            ->whereBetween('created_at', [$dayStart, $dayEnd])
            ->get();

        $created = $skippedExisting = $skippedNoSighting = 0;

        foreach ($checkins as $checkin) {
            $employeeId = $checkin->employee_id;

            $alreadyOut = ActivityEvent::where('employee_id', $employeeId)
                ->where('event_type', 'checkout')
                ->whereBetween('created_at', [$dayStart, $dayEnd])
                ->exists();

            if ($alreadyOut) {
                $skippedExisting++;
                continue;
            }

            // Last sighting = the latest event of ANY kind for this employee
            // today, excluding checkin/checkout themselves (a checkin is an
            // arrival, not a sighting of them still being present, and would
            // otherwise make checkout = checkin + grace for anyone the zone
            // cameras never picked up).
            $lastSeen = ActivityEvent::where('employee_id', $employeeId)
                ->whereNotIn('event_type', ['checkin', 'checkout'])
                ->whereBetween('created_at', [$dayStart, $dayEnd])
                ->max('created_at');

            if (! $lastSeen) {
                // Checked in but never seen by a zone camera. Deriving a
                // departure from nothing would be fabrication, so leave it
                // for HR — the attendance screen shows it as missing.
                $skippedNoSighting++;
                continue;
            }

            $at = Carbon::parse($lastSeen)->addMinutes(self::CHECKOUT_GRACE_MINUTES);
            // Never push a derived checkout past the end of its own day; a
            // late-evening last sighting plus the grace period could
            // otherwise land on tomorrow and disappear from both days' views.
            if ($at->greaterThan($dayEnd)) {
                $at = $dayEnd;
            }

            $this->writeAttendanceEvent($employeeId, 'checkout', $at, [
                'method' => null,           // derived, not observed and not asserted
                'zone' => $checkin->zone,
                'camera_id' => $checkin->camera_id,
            ]);
            $created++;
        }

        Log::info('Derived checkouts', compact('date', 'created', 'skippedExisting', 'skippedNoSighting'));

        return [
            'date' => $date,
            'created' => $created,
            'skipped_existing' => $skippedExisting,
            'skipped_no_sighting' => $skippedNoSighting,
        ];
    }

    /**
     * Record a manual checkin/checkout. $at defaults to now; HR normally
     * supplies the real time after the fact.
     *
     * @return array{ok: bool, status: int, body: array}
     */
    public function recordManual(string $type, int $employeeId, ?string $at, ?int $userId): array
    {
        if (! in_array($type, ['checkin', 'checkout'], true)) {
            return ['ok' => false, 'status' => 422, 'body' => ['message' => 'نوع غير مدعوم']];
        }

        $employee = Employee::find($employeeId);
        if (! $employee) {
            return ['ok' => false, 'status' => 404, 'body' => ['message' => 'الموظف غير موجود']];
        }

        $when = $at ? Carbon::parse($at) : now();

        // One checkin and one checkout per employee per day. Without this a
        // second manual entry would silently create a duplicate and every
        // downstream "arrival time" query would pick an arbitrary one.
        $existing = ActivityEvent::where('employee_id', $employeeId)
            ->where('event_type', $type)
            ->whereBetween('created_at', [$when->copy()->startOfDay(), $when->copy()->endOfDay()])
            ->first();

        if ($existing) {
            return [
                'ok' => false,
                'status' => 409,
                'body' => [
                    'message' => $type === 'checkin'
                        ? 'الموظف لديه تسجيل حضور لهذا اليوم بالفعل'
                        : 'الموظف لديه تسجيل خروج لهذا اليوم بالفعل',
                    'existing_event_id' => $existing->id,
                    'existing_at' => $existing->created_at,
                ],
            ];
        }

        if ($type === 'checkout') {
            // A checkout before that day's checkin would produce negative
            // worked time in every report that subtracts them.
            $checkin = ActivityEvent::where('employee_id', $employeeId)
                ->where('event_type', 'checkin')
                ->whereBetween('created_at', [$when->copy()->startOfDay(), $when->copy()->endOfDay()])
                ->first();

            if ($checkin && $when->lessThan($checkin->created_at)) {
                return [
                    'ok' => false,
                    'status' => 422,
                    'body' => ['message' => 'وقت الخروج قبل وقت الحضور لنفس اليوم'],
                ];
            }
        }

        $event = $this->writeAttendanceEvent($employeeId, $type, $when, [
            'method' => 'manual',
            'recorded_by' => $userId,
        ]);

        return [
            'ok' => true,
            'status' => 201,
            'body' => [
                'message' => $type === 'checkin' ? 'تم تسجيل الحضور يدوياً' : 'تم تسجيل الخروج يدوياً',
                'event' => [
                    'id' => $event->id,
                    'employee_id' => $employeeId,
                    'name' => $employee->name,
                    'event_type' => $type,
                    'at' => $event->created_at,
                    'method' => 'manual',
                ],
            ],
        ];
    }

    /**
     * Hard-delete an attendance row — used when the model credited the wrong
     * employee. Restricted to checkin/checkout so this can't be turned into a
     * way to quietly erase observed activity events.
     *
     * Deleting a checkin also deletes that day's checkout for the same
     * employee: a departure with no arrival is not a state any report can
     * interpret, and leaving it behind would make the person look like they
     * left without ever coming in.
     */
    public function deleteAttendanceEvent(int $eventId): array
    {
        $event = ActivityEvent::find($eventId);

        if (! $event) {
            return ['ok' => false, 'status' => 404, 'body' => ['message' => 'السجل غير موجود']];
        }
        if (! in_array($event->event_type, ['checkin', 'checkout'], true)) {
            return [
                'ok' => false,
                'status' => 422,
                'body' => ['message' => 'يمكن حذف سجلات الحضور والخروج فقط'],
            ];
        }

        $alsoDeleted = [];
        if ($event->event_type === 'checkin' && $event->employee_id) {
            $day = Carbon::parse($event->created_at);
            $checkouts = ActivityEvent::where('employee_id', $event->employee_id)
                ->where('event_type', 'checkout')
                ->whereBetween('created_at', [$day->copy()->startOfDay(), $day->copy()->endOfDay()])
                ->get();
            foreach ($checkouts as $co) {
                $alsoDeleted[] = $co->id;
                $co->delete();
            }
        }

        $event->delete();
        Log::info('Attendance event deleted', ['event_id' => $eventId, 'cascaded_checkouts' => $alsoDeleted]);

        return [
            'ok' => true,
            'status' => 200,
            'body' => [
                'message' => 'تم حذف السجل',
                'deleted_event_id' => $eventId,
                'also_deleted_checkout_ids' => $alsoDeleted,
            ],
        ];
    }

    /**
     * Attendance for one day: every employee, with their checkin/checkout if
     * any. Absent employees are included deliberately — "who hasn't arrived"
     * is the question HR opens this screen to answer, and it can't be
     * answered by a list of events.
     */
    public function dailyAttendance(?string $date = null): array
    {
        $date = $date ?: now()->toDateString();
        $dayStart = Carbon::parse($date)->startOfDay();
        $dayEnd = Carbon::parse($date)->endOfDay();

        $employees = Employee::orderBy('name')->get(['id', 'name', 'job_num', 'Administration', 'position']);

        $events = ActivityEvent::whereNotNull('employee_id')
            ->whereIn('event_type', ['checkin', 'checkout'])
            ->whereBetween('created_at', [$dayStart, $dayEnd])
            ->orderBy('created_at')
            ->get()
            ->groupBy('employee_id');

        // Last sighting per employee, so HR can sanity-check a derived
        // checkout against when the cameras actually last saw the person.
        $lastSeen = ActivityEvent::whereNotNull('employee_id')
            ->whereNotIn('event_type', ['checkin', 'checkout'])
            ->whereBetween('created_at', [$dayStart, $dayEnd])
            ->selectRaw('employee_id, MAX(created_at) as last_seen_at')
            ->groupBy('employee_id')
            ->pluck('last_seen_at', 'employee_id');

        $present = [];
        $absent = [];

        foreach ($employees as $emp) {
            $rows = $events->get($emp->id, collect());
            $checkin = $rows->firstWhere('event_type', 'checkin');
            $checkout = $rows->where('event_type', 'checkout')->last();

            $entry = [
                'employee_id' => $emp->id,
                'name' => $emp->name,
                'job_num' => $emp->job_num,
                'administration' => $emp->Administration,
                'position' => $emp->position,
                'checkin' => $checkin ? [
                    'event_id' => $checkin->id,
                    'at' => $checkin->created_at,
                    'method' => $checkin->method,
                    'confidence' => $checkin->confidence,
                    'is_manual' => $checkin->method === 'manual',
                ] : null,
                'checkout' => $checkout ? [
                    'event_id' => $checkout->id,
                    'at' => $checkout->created_at,
                    'method' => $checkout->method,
                    'is_manual' => $checkout->method === 'manual',
                    // method null on a checkout means neither observed nor
                    // asserted: derived from the last sighting + grace.
                    'is_derived' => $checkout->method === null,
                ] : null,
                'last_seen_at' => $lastSeen->get($emp->id),
            ];

            if ($checkin) {
                $entry['worked_minutes'] = $checkout
                    ? round(Carbon::parse($checkin->created_at)
                        ->diffInSeconds(Carbon::parse($checkout->created_at)) / 60, 1)
                    : null;
                $present[] = $entry;
            } else {
                $absent[] = $entry;
            }
        }

        return [
            'date' => $date,
            'present_count' => count($present),
            'absent_count' => count($absent),
            'grace_minutes' => self::CHECKOUT_GRACE_MINUTES,
            'present' => $present,
            'absent' => $absent,
        ];
    }

    /**
     * Attendance rows carry no video offsets, so start_s/end_s/duration_s are
     * zero — the row's timestamp IS the event. created_at is set explicitly
     * because HR routinely enters a time after the fact.
     */
    private function writeAttendanceEvent(int $employeeId, string $type, Carbon $at, array $extra = []): ActivityEvent
    {
        return ActivityEvent::create(array_merge([
            'camera_id' => null,
            'vision_job_id' => null,
            'employee_id' => $employeeId,
            'confidence' => null,
            'method' => null,
            'recorded_by' => null,
            'event_type' => $type,
            'start_s' => 0,
            'end_s' => 0,
            'duration_s' => 0,
            'zone' => 'entrance',
            'created_at' => $at,
            'updated_at' => $at,
        ], $extra));
    }
}
