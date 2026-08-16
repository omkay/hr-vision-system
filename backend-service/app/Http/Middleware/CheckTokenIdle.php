<?php

namespace App\Http\Middleware;

use Carbon\Carbon;
use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

class CheckTokenIdle
{
    public function handle(Request $request, Closure $next): Response
    {
        $user = $request->user();

        if ($token = $user->currentAccessToken()) {

            // إذا في last_used_at استخدمه، إذا لا استخدم created_at
            $referenceTime = $token->last_used_at ?? $token->created_at;

            $diff = Carbon::parse($referenceTime)->diffInMinutes(now());

            // إذا صار أكثر من ساعة
            if ($diff > 60) {
                $token->delete();

                return response()->json([
                    'message' => 'انتهت صلاحية الجلسة بسبب عدم النشاط، يرجى تسجيل الدخول مجدداً.'
                ], 401);
            }
        }

        return $next($request);
    }
}