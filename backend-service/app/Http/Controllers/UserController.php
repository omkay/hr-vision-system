<?php

namespace App\Http\Controllers;

use App\Models\User;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Hash;

class UserController extends Controller
{
    public function login(Request $request)
    {
        $request->validate([
            'user_name' => 'required|string',
            'password' => 'required|string',
        ]);

        $user = User::where('user_name', $request->user_name)->whereNull('deleted_at')->first();

        if (! $user || ! Hash::check($request->password, $user->password)) {
            return response()->json([
                'message' => 'اسم المستخدم او كلمة المرور غير صحية.'
            ], 401);
        }

        $user->tokens()->delete();

        $token = $user->createToken('desktop-app')->plainTextToken;

        return response()->json([
            'token' => $token,
            'user' => $user
        ]);
    }

    public function logout(Request $request)
    {
        $request->user()->tokens()->delete();

        return response()->json([
            'message' => 'تم تسجيل الخروج بنجاح.'
        ], 200);
    }

    public function register(Request $request)
    {
        $request->validate([
            'user_name' => 'required|string|unique:users,user_name',
            'password' => 'required|confirmed|string|min:6',
            'role_id' => 'required|integer|exists:roles,id'
        ]);

        $user = User::create([
            'user_name' => $request->user_name,
            'password' => $request->password,
            'role_id' => $request->role_id
        ]);

        return response()->json([
            'message' => 'تم إنشاء المستخدم بنجاح',
            'user' => $user,
        ], 200);
    }

    public function update(Request $request, $id)
    {
        $user = User::find($id);

        if (!$user) {
            return response()->json(['message' => 'المستخدم غير موجود'], 404);
        }

        if ($user->role_id == 3) {
            return response()->json(['message' => 'لا يمكنك تعديل بيانات هذا المستخدم'], 401);
        }

        $request->validate([
            'user_name' => 'sometimes|string|unique:users,user_name,' . $id,
            'role_id' => 'sometimes|integer|exists:roles,id'
        ]);

        $user->update($request->only([
            'user_name',
            'role_id'
        ]));

        $user->tokens()->delete();

        return response()->json([
            'message' => 'تم تحديث بيانات المستخدم بنجاح',
            'user' => $user
        ], 200);
    }

    //خطا تصميم بال API لازم نبعت role_name
    public function getUseres (Request $request){
        
        if ($request->user()->role_id == 3){
            $users = User::with('role')
            ->latest()
            ->get()
            ->map(function ($user) {
                return [
                    'id' => $user->id,
                    'role_id' => $user->role ? $user->role->name : 'No Role',
                    'user_name' => $user->user_name,
                ];
            });
        }else{
            $users = User::with('role')
            ->latest()
            ->get()
            ->whereNotIn('role_id', [3])
            ->map(function ($user) {
                return [
                    'id' => $user->id,
                    'role_id' => $user->role ? $user->role->name : 'No Role',
                    'user_name' => $user->user_name,
                ];
            });
        }
        
        return response()->json([
            'message' => 'المستخدمين',
            'users' => $users
        ], 200);
    }

    public function destroy(Request $request, $id)
    {
        $user = User::find($id);

        if (!$user) {
            return response()->json(['message' => 'الحساب غير موجود'], 404);
        }

        if ($user->id === $request->user()->id) {
            return response()->json(['message' => 'لا يمكنك حذف حسابك'], 403);
        }

        $user->tokens()->delete();
        $user->delete();

        return response()->json([
            'message' => 'تم حذف الموظف بنجاح',
        ], 200);
    }

    public function reset_pass(Request $request, $id){
        $user = User::find($id);

        if (!$user) {
            return response()->json(['message' => 'المستخدم غير موجود'], 404);
        }

        $request->validate([
            'new_password' => 'required|confirmed|string|min:6',
        ]);

        $user->update([
            'password' => $request->new_password
        ]);

        $user->tokens()->delete();

        return response()->json([
            'message' => 'تم تعديل كلمة المرور بنجاح',
        ], 200);
    }
}