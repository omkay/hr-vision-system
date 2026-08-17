<?php

namespace App\Http\Controllers;

use App\Jobs\EnrollEmployeeInVisionService;
use App\Models\Employee;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Facades\Storage;
use OpenApi\Attributes as OA;

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

    #[OA\Get(
        path: '/employees/{id}/photos',
        summary: 'List an employee\'s enrolled face/body photos',
        tags: ['Employee Photos'],
        security: [['bearerAuth' => []]],
        parameters: [new OA\Parameter(name: 'id', in: 'path', required: true, schema: new OA\Schema(type: 'integer'))],
        responses: [new OA\Response(response: 200, description: 'Enrolled photos.')],
    )]
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
    #[OA\Post(
        path: '/employees/{id}/photos',
        summary: 'Add face/body photos for an employee (queues vision-service enrollment)',
        tags: ['Employee Photos'],
        security: [['bearerAuth' => []]],
        parameters: [new OA\Parameter(name: 'id', in: 'path', required: true, schema: new OA\Schema(type: 'integer'))],
        requestBody: new OA\RequestBody(
            required: true,
            content: new OA\MediaType(
                mediaType: 'multipart/form-data',
                schema: new OA\Schema(properties: [
                    new OA\Property(property: 'face_images[]', type: 'array', items: new OA\Items(type: 'string', format: 'binary')),
                    new OA\Property(property: 'body_images[]', type: 'array', items: new OA\Items(type: 'string', format: 'binary')),
                ]),
            ),
        ),
        responses: [
            new OA\Response(response: 201, description: 'Photos added, enrollment queued.'),
            new OA\Response(response: 422, description: 'No face or body image provided.'),
        ],
    )]
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

    #[OA\Delete(
        path: '/employees/{id}/photos/{photoId}',
        summary: 'Delete an employee photo (re-queues vision-service enrollment)',
        tags: ['Employee Photos'],
        security: [['bearerAuth' => []]],
        parameters: [
            new OA\Parameter(name: 'id', in: 'path', required: true, schema: new OA\Schema(type: 'integer')),
            new OA\Parameter(name: 'photoId', in: 'path', required: true, schema: new OA\Schema(type: 'integer')),
        ],
        responses: [new OA\Response(response: 200, description: 'Photo deleted.')],
    )]
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
