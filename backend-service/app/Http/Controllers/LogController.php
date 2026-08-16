<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Spatie\Activitylog\Models\Activity;

class LogController extends Controller{
    public function index(Request $request)
    {
        $logs = Activity::with('causer')
            ->latest()
            ->paginate(20);

        $data = $logs->getCollection()->map(function ($log) {

            $properties = $log->properties ? $log->properties->toArray() : [];

            $attributes = $properties['attributes'] ?? [];
            $old = $properties['old'] ?? [];

            $changes = [];

            switch ($log->event) {

                // ✅ CREATE
                case 'created':
                    foreach ($attributes as $key => $value) {
                        $changes[$key] = [
                            'old' => null,
                            'new' => $value
                        ];
                    }
                    break;

                // ✅ UPDATE
                case 'updated':
                    foreach ($attributes as $key => $newValue) {
                        $oldValue = $old[$key] ?? null;

                        if ($oldValue != $newValue) {
                            $changes[$key] = [
                                'old' => $oldValue,
                                'new' => $newValue
                            ];
                        }
                    }
                    break;

                // ✅ DELETE
                case 'deleted':
                    foreach ($old as $key => $value) {
                        $changes[$key] = [
                            'old' => $value,
                            'new' => null
                        ];
                    }
                    break;
            }

            return [
                'id' => $log->id,
                'action' => $log->description,
                'event' => $log->event, // 🔥 مهم للفرونت
                'model' => class_basename($log->subject_type),
                'model_id' => $log->subject_id,
                'by' => optional($log->causer)->user_name ?? 'System',
                'changes' => $changes,
                'date' => $log->created_at->format('Y-m-d H:i')
            ];
        });

        return response()->json([
            'message' => 'Logs List',
            'pagination' => [
                'current_page' => $logs->currentPage(),
                'last_page' => $logs->lastPage(),
                'per_page' => $logs->perPage(),
                'total' => $logs->total(),
                'has_more' => $logs->hasMorePages(),
            ],
            'data' => $data
        ]);
    }
}