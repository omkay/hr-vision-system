<?php

namespace App\Http\Controllers;

use App\Models\Camera;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Storage;

class CameraController extends Controller
{
    public function add_camera(Request $request)
    {
        $request->validate([
            'name' => 'required|string|max:255',
            'zone_id' => 'required|exists:zones,id',
            'video' => 'required|file|mimes:mp4,mov,avi,mkv|max:512000'
        ]);

        $videoPath = $request->file('video')
            ->store('cameras', 'public');

        $camera = Camera::create([
            'name' => $request->name,
            'zone_id' => $request->zone_id,
            'video' => $videoPath,
        ]);

        return response()->json([
            'message' => 'تمت إضافة الكاميرا بنجاح',

            'camera' => [
                'id' => $camera->id,
                'name' => $camera->name,
                'zone' => $camera->zone->name,
                'video_url' => asset('storage/' . $camera->video),
            ]
        ], 201);
    }

    public function get_cameras()
    {
        $cameras = Camera::with('zone')
            ->latest()
            ->get();

        $data = $cameras->map(function ($camera) {

            return [
                'id' => $camera->id,
                'name' => $camera->name,
                'video_url' => $camera->video? asset('storage/' . $camera->video) : null,
                'zone' => [
                    'id' => $camera->zone->id,
                    'name' => $camera->zone->name,
                ]
            ];
        });

        return response()->json([
            'message' => 'الكاميرات',
            'cameras' => $data
        ], 200);
    }

    public function update_camera(Request $request, $id)
    {
        $camera = Camera::findOrFail($id);

        $request->validate([
            'name' => 'required|string|max:255',
            'zone_id' => 'required|exists:zones,id',
            'video' => 'nullable|file|mimes:mp4,mov,avi,mkv|max:512000'
        ]);

        // إذا في فيديو جديد
        if ($request->hasFile('video')) {

            // حذف القديم
            if ($camera->video &&
                Storage::disk('public')->exists($camera->video)) {

                Storage::disk('public')->delete($camera->video);
            }

            // تخزين الجديد
            $videoPath = $request->file('video')
                ->store('cameras', 'public');

            $camera->video = $videoPath;
        }

        $camera->name = $request->name;
        $camera->zone_id = $request->zone_id;

        $camera->save();

        return response()->json([
            'message' => 'تم تعديل الكاميرا بنجاح',

            'camera' => [
                'id' => $camera->id,
                'name' => $camera->name,
                'zone' => $camera->zone->name,
                'video_url' => $camera->video ? asset('storage/' . $camera->video) : null,
            ]
        ], 200);
    }

    public function delete_camera($id)
    {
        $camera = Camera::findOrFail($id);

        if ($camera->video &&
            Storage::disk('public')->exists($camera->video)) {

            Storage::disk('public')->delete($camera->video);
        }

        $camera->delete();

        return response()->json([
            'message' => 'تم حذف الكاميرا بنجاح'
        ], 200);
    }
}
