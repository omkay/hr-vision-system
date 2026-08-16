<?php

namespace App\Http\Controllers;

use App\Models\Role;
use Illuminate\Http\Request;

class RoleController extends Controller
{
    public function store(Request $request)
    {
        $request->validate([
            'name'             => 'required|string|unique:roles',
        ]);

        $role = Role::create([
            'name' => $request->name
        ]);

        return response()->json([
            'message' => 'تم إضافة الدور بنجاح',
            'role' => $role
        ], 200);
    }

    public function update(Request $request, $id)
    {
        $role = Role::find($id);

        if (!$role) {
            return response()->json(['message' => 'الدور غير موجود'], 404);
        }

        $request->validate([
            'name'          => 'sometimes|string|unique:roles,name,' . $id,
        ]);

        $role->update([
            'name' => $request->name
            ]);

        return response()->json([
            'message' => 'تم تحديث بيانات الدور بنجاح',
            'role' => $role
        ], 200);
    }

    public function destroy($id)
    {
        $role = Role::find($id);

        if (!$role) {
            return response()->json(['message' => 'الدور غير موجود'], 404);
        }

        $role->delete();

        return response()->json([
            'message' => 'تم حذف الدور بنجاح'
        ], 200);
    }
}
