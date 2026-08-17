<?php

namespace App\Http\Controllers;

use App\Models\Employee;
use App\Models\Zone;
use Illuminate\Http\Request;
use OpenApi\Attributes as OA;

class ZoneController extends Controller
{
    #[OA\Post(
        path: '/zone/add',
        summary: 'Add a zone',
        tags: ['Zones'],
        security: [['bearerAuth' => []]],
        requestBody: new OA\RequestBody(
            required: true,
            content: new OA\JsonContent(
                required: ['name'],
                properties: [
                    new OA\Property(property: 'name', type: 'string', example: 'Main Lobby'),
                    new OA\Property(property: 'zone_type', type: 'string', enum: ['work_area', 'common_area'], example: 'common_area'),
                ],
            ),
        ),
        responses: [new OA\Response(response: 200, description: 'Zone created.')],
    )]
    public function store(Request $request)
    {
        $request->validate([
            'name' => 'required|string|unique:zones,name,',
            'zone_type' => 'nullable|in:work_area,common_area',
        ]);

        $Zone = Zone::create([
            'name' => $request->name,
            'zone_type' => $request->zone_type ?? 'work_area',
        ]);

        return response()->json([
            'message' => 'تم إضافة المنطقة بنجاح',
            'Zone' => $Zone
        ], 200);
    }

    public function update(Request $request, $id)
    {
        $Zone = Zone::find($id);

        if (!$Zone) {
            return response()->json(['message' => 'المنطقة غير موجود'], 404);
        }

        $request->validate([
            'name'   => 'sometimes|string|unique:zones,name,' . $id,
            'zone_type' => 'sometimes|in:work_area,common_area',
        ]);

        // Only write fields that were actually sent — previously this always
        // overwrote 'name' even when omitted (since validation was
        // "sometimes" but the update call wasn't conditional), silently
        // nulling it out. Fixed here rather than carried forward into zone_type too.
        $Zone->update($request->only(['name', 'zone_type']));

        return response()->json([
            'message' => 'تم تحديث بيانات المنطقة بنجاح',
            'Zone' => $Zone
        ], 200);
    }

    public function destroy($id)
    {
        $Zone = Zone::find($id);

        if (!$Zone) {
            return response()->json(['message' => 'المنطقة غير موجودة'], 404);
        }

        $Zone->delete();

        return response()->json([
            'message' => 'تم حذف المنطقة بنجاح'
        ], 200);
    }

    #[OA\Get(
        path: '/zone/get',
        summary: 'List zones',
        tags: ['Zones'],
        security: [['bearerAuth' => []]],
        responses: [new OA\Response(response: 200, description: 'All zones.')],
    )]
    public function get_zones (){
        $Zone = Zone::latest()->get();

        return response()->json([
            'message' => 'المناطق',
            'Zone' => $Zone
        ], 200);
    }

    public function sync(Request $request)
    {
        $request->validate([
            'employee_id' => 'required|exists:employees,id',
            'zone_ids' => 'array',
            'zone_ids.*' => 'exists:zones,id'
        ]);

        $employee = Employee::findOrFail($request->employee_id);

        // مزامنة المناطق
        $employee->zones()->sync($request->zone_ids);

        // تحميل العلاقات بعد التحديث
        $employee->load('zones');

        return response()->json([
            'message' => 'تمت مزامنة المناطق بنجاح',

            'employee' => [
                'id' => $employee->id,
                'name' => $employee->name,

                'zones' => $employee->zones->map(function ($zone) {
                    return [
                        'id' => $zone->id,
                        'name' => $zone->name,
                    ];
                })
            ]
        ], 200);
    }
}
