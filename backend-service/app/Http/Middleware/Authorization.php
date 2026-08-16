<?php

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

class Authorization
{
    /**
     * Handle an incoming request.
     *
     * @param  \Illuminate\Http\Request  $request
     * @param  \Closure  $next
     * @param  string  ...$roles  <-- هنا نستقبل الأدوار كمصفوفة
     */
    public function handle(Request $request, Closure $next, ...$roles): Response
    {
        $user = $request->user();

        // نتحقق إذا كان دور المستخدم موجوداً ضمن مصفوفة الأدوار المسموحة
        if (!$user || !in_array($user->role->name, $roles)) {
            return response()->json([
                'message' => 'ليس لديك صلاحية الوصول لهذه العملية'
            ], 403);
        }

        return $next($request);
    }
}