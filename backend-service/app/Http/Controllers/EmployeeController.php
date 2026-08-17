<?php

namespace App\Http\Controllers;

use App\Jobs\EnrollEmployeeInVisionService;
use App\Models\Employee;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Facades\Storage;
use Illuminate\Validation\Rule;
use OpenApi\Attributes as OA;

class EmployeeController extends Controller
{
    /**
     * Queue a vision-service enrollment without ever letting a queue/dispatch
     * failure fail the employee create/update request itself — the employee
     * record is the source of truth; enrollment is a best-effort side effect.
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

    #[OA\Post(
        path: '/employees/add',
        summary: 'Add an employee',
        tags: ['Employees'],
        security: [['bearerAuth' => []]],
        requestBody: new OA\RequestBody(
            required: true,
            content: new OA\MediaType(
                mediaType: 'multipart/form-data',
                schema: new OA\Schema(
                    required: ['name', 'Administration', 'job_num', 'position', 'start_date', 'start_time', 'end_time'],
                    properties: [
                        new OA\Property(property: 'name', type: 'string'),
                        new OA\Property(property: 'Administration', type: 'string'),
                        new OA\Property(property: 'department', type: 'string'),
                        new OA\Property(property: 'job_num', type: 'integer'),
                        new OA\Property(property: 'position', type: 'string'),
                        new OA\Property(property: 'direct_maneger', type: 'string'),
                        new OA\Property(property: 'start_date', type: 'string', format: 'date'),
                        new OA\Property(property: 'start_time', type: 'string'),
                        new OA\Property(property: 'end_time', type: 'string'),
                        new OA\Property(property: 'work_site', type: 'string'),
                        new OA\Property(property: 'sheft', type: 'string'),
                        new OA\Property(property: 'phone_num', type: 'string'),
                        new OA\Property(property: 'card_id', type: 'string'),
                        new OA\Property(property: 'image', type: 'string', format: 'binary', description: 'Optional — triggers async vision-service enrollment if provided.'),
                    ],
                ),
            ),
        ),
        responses: [new OA\Response(response: 201, description: 'Employee created.')],
    )]
    public function store(Request $request)
    {
        $request->validate([
            'name' => 'required|string|max:255',
            'Administration' => 'required|string|max:255',
            'department' => 'nullable|string|max:255',
            'job_num' => [
                'required',
                'integer',
                Rule::unique('employees')->whereNull('deleted_at')
            ],
            'position' => 'required|string|max:255',
            'direct_maneger' => 'nullable|string|max:255',
            'start_date' => 'required|date',
            'start_time' => 'required',
            'end_time' => 'required',
            'work_site' => 'nullable|string|max:255',
            'sheft' => 'nullable|string|max:255',
            'phone_num' => 'nullable|string|max:50',
            'card_id' => [
                'nullable',
                'string',
                'max:255',
                Rule::unique('employees')->whereNull('deleted_at')
            ],
            'image' => 'nullable|image|mimes:jpg,jpeg,png,webp|max:2048',
        ]);

        $imagePath = null;

        if ($request->hasFile('image')) {
            $imagePath = $request->file('image')
                ->store('employees', 'public');
        }
        $employee = Employee::create([
            'name' => $request->name,
            'Administration' => $request->Administration,
            'department' => $request->department,
            'job_num' => $request->job_num,
            'position' => $request->position,
            'direct_maneger' => $request->direct_maneger,
            'start_date' => $request->start_date,
            'start_time' => $request->start_time,
            'end_time' => $request->end_time,
            'work_site' => $request->work_site,
            'sheft' => $request->sheft,
            'phone_num' => $request->phone_num,
            'card_id' => $request->card_id,
            'image' => $imagePath,
        ]);

        if ($imagePath) {
            $this->enqueueVisionEnrollment($employee->id);
        }

        return response()->json([
            'message' => 'تم إضافة الموظف بنجاح',
            'employee' => $employee
        ], 201);
    }

    
    public function update(Request $request, $id){
        $employee = Employee::findOrFail($id);

        $request->validate([
            'name' => 'required|string|max:255',
            'Administration' => 'required|string|max:255',
            'department' => 'nullable|string|max:255',
            'job_num' => [
                'required',
                'integer',
                Rule::unique('employees')
                    ->ignore($employee->id)
                    ->whereNull('deleted_at')
            ],
            'position' => 'required|string|max:255',
            'direct_maneger' => 'nullable|string|max:255',
            'start_date' => 'required|date',
            'start_time' => 'required',
            'end_time' => 'required',
            'work_site' => 'nullable|string|max:255',
            'sheft' => 'nullable|string|max:255',
            'phone_num' => 'nullable|string|max:50',
            'card_id' => [
                'nullable',
                'string',
                'max:255',
                Rule::unique('employees')
                    ->ignore($employee->id)
                    ->whereNull('deleted_at')
            ],
            'image' => 'nullable|image|mimes:jpg,jpeg,png,webp|max:2048',
        ]);

        $newImageUploaded = $request->hasFile('image');

        if ($newImageUploaded) {

            if ($employee->image &&
                Storage::disk('public')->exists($employee->image)) {

                Storage::disk('public')->delete($employee->image);
            }

            $imagePath = $request->file('image')
                ->store('employees', 'public');

            $employee->image = $imagePath;
        }

        $employee->update([
            'name' => $request->name,
            'Administration' => $request->Administration,
            'department' => $request->department,
            'job_num' => $request->job_num,
            'position' => $request->position,
            'direct_maneger' => $request->direct_maneger,
            'start_date' => $request->start_date,
            'start_time' => $request->start_time,
            'end_time' => $request->end_time,
            'work_site' => $request->work_site,
            'sheft' => $request->sheft,
            'phone_num' => $request->phone_num,
            'card_id' => $request->card_id,
        ]);

        if ($newImageUploaded) {
            $this->enqueueVisionEnrollment($employee->id);
        }

        return response()->json([
            'message' => 'تم تعديل بيانات الموظف بنجاح',

            'employee' => [
                'id' => $employee->id,
                'name' => $employee->name,
                'Administration' => $employee->Administration,
                'department' => $employee->department,
                'job_num' => $employee->job_num,
                'position' => $employee->position,
                'direct_maneger' => $employee->direct_maneger,
                'start_date' => $employee->start_date,
                'start_time' => $employee->start_time,
                'end_time' => $employee->end_time,
                'work_site' => $employee->work_site,
                'sheft' => $employee->sheft,
                'phone_num' => $employee->phone_num,
                'card_id' => $employee->card_id,
                'qr_token' => $employee->qr_token,
                'image_url' => asset('storage/' . $employee->image),
            ]
        ], 200);
    }


    public function destroy($id){
        $employee = Employee::findOrFail($id);

        if ($employee->image &&
            Storage::disk('public')->exists($employee->image)) {

            Storage::disk('public')->delete($employee->image);
        }

        $employee->delete();

        return response()->json([
            'message' => 'تم حذف الموظف بنجاح'
        ], 200);
    }

    #[OA\Get(
        path: '/employees/get',
        summary: 'List employees',
        tags: ['Employees'],
        security: [['bearerAuth' => []]],
        responses: [new OA\Response(response: 200, description: 'All employees.')],
    )]
    public function get_employees (){

        $employees = Employee::latest()->get();

            $data = $employees->map(function ($employee) {

            return [
                'id' => $employee->id,
                'name' => $employee->name,
                'Administration' => $employee->Administration,
                'department' => $employee->department,
                'job_num' => $employee->job_num,
                'position' => $employee->position,
                'direct_maneger' => $employee->direct_maneger,
                'start_date' => $employee->start_date,
                'start_time' => $employee->start_time,
                'end_time' => $employee->end_time,
                'work_site' => $employee->work_site,
                'sheft' => $employee->sheft,
                'phone_num' => $employee->phone_num,
                'card_id' => $employee->card_id,
                'qr_token' => $employee->qr_token,
                'image_url' => $employee->image ? asset('storage/' . $employee->image) : null,
                'zones' => $employee->zones->map(function ($zone) {
                    return [
                        'id' => $zone->id,
                        'name' => $zone->name,
                    ];
                })->values()
            ];
        });

        return response()->json([
            'message' => 'الموظفين',
            'employees' => $data
        ], 200);
    }

    public function showProfile($token)
    {
        $employee = Employee::where('qr_token', $token)->firstOrFail();

        return view('employee.profile', compact('employee'));
    }
}
