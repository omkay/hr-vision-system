<?php

namespace App\Http\Controllers;

use App\Models\Employee;
use App\Models\EmployeeCheckin;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;

/**
 * Daily attendance checkin — "was this employee present today" — distinct
 * from the (separate, not-yet-built) zone-based activity tracking feature.
 * See INTEGRATION-TODO-multi-photo-enrollment.md section 2 for the design.
 *
 * Identity is discovered FROM the photo, not supplied by the caller —
 * employees have no login accounts of their own in this app, so a kiosk or
 * personal device has no other way to know who's checking in. A checkin is
 * a deliberate, synchronous action: one photo, matched immediately, accepted
 * or rejected in the same request — no queue, no polling, unlike enrollment.
 */
class CheckinController extends Controller
{
    public function store(Request $request)
    {
        $request->validate([
            'photo' => 'required|image|mimes:jpg,jpeg,png,webp|max:2048',
        ]);

        $photoPath = $request->file('photo')->store('checkins', 'public');
        $photoUrl = rtrim(config('app.internal_url'), '/') . '/storage/' . $photoPath;
        $visionUrl = rtrim(config('services.vision.url'), '/') . '/checkin';

        try {
            $response = Http::timeout(30)->post($visionUrl, [
                'image_path' => $photoUrl,
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
                'message' => 'تعذر التعرف على الصورة، حاول مرة أخرى',
            ], 502);
        }

        $result = $response->json();
        $matchedJobNum = $result['employee_id'] ?? 'UNKNOWN';

        // The vision service itself already applies face_thr/reid_thr and
        // only returns UNKNOWN when nothing clears its own threshold — no
        // extra confidence gate needed here, just trust its verdict.
        if ($matchedJobNum === 'UNKNOWN') {
            return response()->json([
                'message' => 'لم يتم التعرف على الموظف، حاول مرة أخرى',
                'match' => $result,
            ], 422);
        }

        $employee = Employee::where('job_num', $matchedJobNum)->first();

        if (! $employee) {
            // The gallery has an identity Hr_SmartPay doesn't recognize — the
            // employee record was likely deleted after enrollment. Flag it
            // rather than silently checking in a ghost employee_id.
            Log::warning('Checkin matched an unknown job_num', [
                'matched_job_num' => $matchedJobNum,
            ]);

            return response()->json([
                'message' => 'لم يتم العثور على بيانات الموظف المطابق',
            ], 422);
        }

        $today = now()->toDateString();
        $existing = EmployeeCheckin::where('employee_id', $employee->id)
            ->where('date', $today)
            ->first();

        if ($existing) {
            return response()->json([
                'message' => 'تم تسجيل حضورك اليوم مسبقاً',
                'checkin' => [
                    'employee_id' => $employee->id,
                    'name' => $employee->name,
                    'checked_in_at' => $existing->checked_in_at,
                ],
            ], 409);
        }

        $checkin = EmployeeCheckin::create([
            'employee_id' => $employee->id,
            'date' => $today,
            'checked_in_at' => now(),
            'confidence' => $result['confidence'] ?? 0,
            'method' => $result['method'] ?? 'face',
            'photo_path' => $photoPath,
        ]);

        return response()->json([
            'message' => 'تم تسجيل الحضور بنجاح',
            'checkin' => [
                'employee_id' => $employee->id,
                'name' => $employee->name,
                'date' => $checkin->date->format('Y-m-d'),
                'checked_in_at' => $checkin->checked_in_at,
                'confidence' => $checkin->confidence,
                'method' => $checkin->method,
            ],
        ], 201);
    }

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
