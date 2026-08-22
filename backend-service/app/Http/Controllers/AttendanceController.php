<?php

namespace App\Http\Controllers;

use App\Services\AttendanceService;
use Illuminate\Http\Request;
use OpenApi\Attributes as OA;

/**
 * Daily attendance for HR: the present/absent view, and the manual overrides
 * that make the automatic pipeline usable in practice.
 *
 * The manual endpoints are not a convenience. Face recognition fails on real
 * people for reasons no threshold addresses — an injury, a marked change in
 * appearance — and attendance drives payroll, so every automatic decision has
 * to be correctable by a human. See AttendanceService for how manual rows stay
 * distinguishable from observed ones.
 */
class AttendanceController extends Controller
{
    public function __construct(private AttendanceService $attendance)
    {
    }

    #[OA\Get(
        path: '/attendance',
        summary: "One day's attendance — present and absent employees",
        description: 'Every employee for the given date with their checkin/checkout if recorded. '
            . 'Absent employees are included on purpose: "who has not arrived" is the question '
            . 'this screen exists to answer and cannot be derived from a list of events. '
            . 'Each entry marks whether the time was observed (`method` face/reid), asserted by '
            . 'a person (`is_manual`), or derived from the last sighting (`is_derived`).',
        tags: ['Attendance'],
        security: [['bearerAuth' => []]],
        parameters: [
            new OA\Parameter(name: 'date', in: 'query', required: false, description: 'Y-m-d. Defaults to today.', schema: new OA\Schema(type: 'string', format: 'date')),
        ],
        responses: [new OA\Response(response: 200, description: 'Attendance for the date.')],
    )]
    public function index(Request $request)
    {
        $request->validate(['date' => 'nullable|date']);

        return response()->json($this->attendance->dailyAttendance($request->input('date')));
    }

    #[OA\Post(
        path: '/attendance/checkin',
        summary: 'Record a checkin by hand',
        description: 'For when the cameras missed someone or credited the wrong person. '
            . 'Stored with method=manual and the acting user in recorded_by, so reports can '
            . 'always separate observed attendance from asserted attendance. '
            . 'One checkin per employee per day — a duplicate returns 409 with the existing row.',
        tags: ['Attendance'],
        security: [['bearerAuth' => []]],
        responses: [
            new OA\Response(response: 201, description: 'Recorded.'),
            new OA\Response(response: 409, description: 'Employee already has a checkin that day.'),
            new OA\Response(response: 404, description: 'Unknown employee.'),
        ],
    )]
    public function storeCheckin(Request $request)
    {
        return $this->manual($request, 'checkin');
    }

    #[OA\Post(
        path: '/attendance/checkout',
        summary: 'Record a checkout by hand',
        description: 'Same contract as the manual checkin. Rejected with 422 if the time is '
            . 'earlier than that day\'s checkin, which would make worked-time come out negative.',
        tags: ['Attendance'],
        security: [['bearerAuth' => []]],
        responses: [
            new OA\Response(response: 201, description: 'Recorded.'),
            new OA\Response(response: 409, description: 'Employee already has a checkout that day.'),
            new OA\Response(response: 422, description: 'Checkout precedes that day\'s checkin.'),
        ],
    )]
    public function storeCheckout(Request $request)
    {
        return $this->manual($request, 'checkout');
    }

    private function manual(Request $request, string $type)
    {
        $request->validate([
            'employee_id' => 'required|integer|exists:employees,id',
            // Free-form datetime so HR can enter a time earlier in the day —
            // the common case, since corrections happen after the fact.
            'at' => 'nullable|date',
        ]);

        $result = $this->attendance->recordManual(
            $type,
            (int) $request->input('employee_id'),
            $request->input('at'),
            $request->user()?->id,
        );

        return response()->json($result['body'], $result['status']);
    }

    #[OA\Delete(
        path: '/attendance/event/{id}',
        summary: 'Delete a checkin or checkout record',
        description: 'Hard delete, for when the pipeline attributed attendance to the wrong '
            . 'employee. Only checkin/checkout rows can be deleted — observed activity events '
            . '(presence/working/phone_use/interaction) are not removable through this endpoint. '
            . "Deleting a checkin also removes that day's checkout for the same employee, since "
            . 'a departure with no arrival is not a state any report can interpret.',
        tags: ['Attendance'],
        security: [['bearerAuth' => []]],
        parameters: [new OA\Parameter(name: 'id', in: 'path', required: true, schema: new OA\Schema(type: 'integer'))],
        responses: [
            new OA\Response(response: 200, description: 'Deleted.'),
            new OA\Response(response: 404, description: 'No such record.'),
            new OA\Response(response: 422, description: 'Not a checkin/checkout row.'),
        ],
    )]
    public function destroy($id)
    {
        $result = $this->attendance->deleteAttendanceEvent((int) $id);

        return response()->json($result['body'], $result['status']);
    }

    #[OA\Post(
        path: '/attendance/finalize',
        summary: 'Derive missing checkouts for a date',
        description: 'Creates a checkout for every employee who checked in that day and has '
            . 'none yet, at (last sighting + grace minutes). Idempotent — employees who already '
            . 'have a checkout, manual or otherwise, are untouched, so this is safe to re-run '
            . 'after each processing job. Employees who checked in but were never seen by a zone '
            . 'camera are skipped rather than given a fabricated departure time.',
        tags: ['Attendance'],
        security: [['bearerAuth' => []]],
        responses: [new OA\Response(response: 200, description: 'Counts of created/skipped.')],
    )]
    public function finalize(Request $request)
    {
        $request->validate(['date' => 'nullable|date']);

        return response()->json($this->attendance->deriveCheckouts($request->input('date')));
    }
}
