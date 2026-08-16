<?php

namespace App\Http\Controllers;

use App\Jobs\EnrollEmployeeInVisionService;
use App\Models\Employee;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Facades\Storage;

class EmployeePhotoController extends Controller
{
    /**
     * Same best-effort dispatch pattern as EmployeeController — a queue
     * failure should never fail the HTTP request that triggered it.
     */
    private function enqueueVisionEnrollment(int $employeeId): void
    {
        try {
            EnrollEmployeeInVisionService::dispatch($employeeId);
        } catch (\Throwable $e) {
            Log::warning('Could not queue vision enrollment', [
                'employee_id' => $employeeId,
                'error' => $e->getMessage(),
            ]);
        }
    }

    public function index($employeeId)
    {
        $employee = Employee::findOrFail($employeeId);

        return response()->json([
            'message' => 'صور الموظف',
            'photos' => $employee->photos()->get()->map(fn ($photo) => [
                'id' => $photo->id,
                'type' => $photo->type,
                'url' => asset('storage/' . $photo->path),
            ]),
        ], 200);
    }

    /**
     * Add one or more face/body photos for an employee, on top of whatever
     * they already have. Additive — this does not replace existing photos,
     * use destroy() to remove a specific one first if needed.
     */
    public function store(Request $request, $employeeId)
    {
        $employee = Employee::findOrFail($employeeId);

        $request->validate([
            'face_images' => 'nullable|array',
            'face_images.*' => 'image|mimes:jpg,jpeg,png,webp|max:2048',
            'body_images' => 'nullable|array',
            'body_images.*' => 'image|mimes:jpg,jpeg,png,webp|max:2048',
        ]);

        if (!$request->hasFile('face_images') && !$request->hasFile('body_images')) {
            return response()->json([
                'message' => 'يجب رفع صورة وجه واحدة على الأقل أو صورة جسم واحدة على الأقل',
            ], 422);
        }

        $created = [];

        foreach ($request->file('face_images', []) as $file) {
            $path = $file->store('employees/photos', 'public');
            $created[] = $employee->photos()->create(['path' => $path, 'type' => 'face']);
        }

        foreach ($request->file('body_images', []) as $file) {
            $path = $file->store('employees/photos', 'public');
            $created[] = $employee->photos()->create(['path' => $path, 'type' => 'body']);
        }

        $this->enqueueVisionEnrollment($employee->id);

        return response()->json([
            'message' => 'تمت إضافة الصور بنجاح',
            'photos' => collect($created)->map(fn ($photo) => [
                'id' => $photo->id,
                'type' => $photo->type,
                'url' => asset('storage/' . $photo->path),
            ]),
        ], 201);
    }

    public function destroy($employeeId, $photoId)
    {
        $employee = Employee::findOrFail($employeeId);
        $photo = $employee->photos()->findOrFail($photoId);

        if (Storage::disk('public')->exists($photo->path)) {
            Storage::disk('public')->delete($photo->path);
        }

        $photo->delete();

        // Re-enroll so the vision-service gallery drops the deleted photo's
        // embedding too, rather than going stale until the next upload.
        $this->enqueueVisionEnrollment($employee->id);

        return response()->json([
            'message' => 'تم حذف الصورة بنجاح',
        ], 200);
    }
}
