<?php

namespace App\Http\Controllers;

use App\Models\Camera;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Storage;
use OpenApi\Attributes as OA;

class CameraController extends Controller
{
    #[OA\Post(
        path: '/camera/add',
        summary: 'Register a camera with its uploaded footage',
        tags: ['Cameras'],
        security: [['bearerAuth' => []]],
        requestBody: new OA\RequestBody(
            required: true,
            content: new OA\MediaType(
                mediaType: 'multipart/form-data',
                schema: new OA\Schema(
                    required: ['name', 'zone_id', 'video'],
                    properties: [
                        new OA\Property(property: 'name', type: 'string', example: 'Main Entrance'),
                        new OA\Property(property: 'zone_id', type: 'integer', example: 1),
                        new OA\Property(property: 'video', type: 'string', format: 'binary', description: 'mp4/mov/avi/mkv, up to 500MB.'),
                        new OA\Property(property: 'is_checkin', type: 'integer', enum: [0, 1], example: 0, description: 'Flag this camera as a checkin camera (e.g. an entrance) — 1 = checkin camera, 0 = regular zone camera. Multiple cameras may be flagged at once — see /process-sequence, which auto-selects and processes every flagged camera\'s checkin video before any zone camera runs, so the daily body-fingerprint gallery is seeded first. Defaults to 0. Accepts 1/0, true/false in the request; every response (this endpoint, /camera/get, /camera/update/{id}) returns it as 1/0.'),
                    ],
                ),
            ),
        ),
        responses: [new OA\Response(response: 201, description: 'Camera created.')],
    )]
    public function add_camera(Request $request)
    {
        $request->validate([
            'name' => 'required|string|max:255',
            'zone_id' => 'required|exists:zones,id',
            'video' => 'required|file|mimes:mp4,mov,avi,mkv|max:512000',
            'is_checkin' => 'nullable|boolean',
        ]);

        $videoPath = $request->file('video')
            ->store('cameras', 'public');

        $camera = Camera::create([
            'name' => $request->name,
            'zone_id' => $request->zone_id,
            'video' => $videoPath,
            'is_checkin' => $request->boolean('is_checkin') ? 1 : 0,
        ]);

        return response()->json([
            'message' => 'تمت إضافة الكاميرا بنجاح',

            'camera' => [
                'id' => $camera->id,
                'name' => $camera->name,
                'zone' => $camera->zone->name,
                'video_url' => asset('storage/' . $camera->video),
                'is_checkin' => $camera->is_checkin,
            ]
        ], 201);
    }

    #[OA\Get(
        path: '/camera/get',
        summary: 'List cameras',
        tags: ['Cameras'],
        security: [['bearerAuth' => []]],
        responses: [new OA\Response(response: 200, description: 'All cameras with their zone and video URL.')],
    )]
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
                'is_checkin' => $camera->is_checkin,
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
            'video' => 'nullable|file|mimes:mp4,mov,avi,mkv|max:512000',
            'is_checkin' => 'nullable|boolean',
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

        // Only touch is_checkin if the caller actually sent it — an admin
        // UI that hasn't been updated to include this field yet (or any
        // other partial-edit form) shouldn't silently flip a camera back to
        // is_checkin=false just by editing its name/zone.
        if ($request->has('is_checkin')) {
            $camera->is_checkin = $request->boolean('is_checkin') ? 1 : 0;
        }

        $camera->save();

        return response()->json([
            'message' => 'تم تعديل الكاميرا بنجاح',

            'camera' => [
                'id' => $camera->id,
                'name' => $camera->name,
                'zone' => $camera->zone->name,
                'video_url' => $camera->video ? asset('storage/' . $camera->video) : null,
                'is_checkin' => $camera->is_checkin,
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
