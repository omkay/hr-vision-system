<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

// Lets a camera be flagged, at creation/edit time, as one of the system's
// checkin cameras (e.g. an entrance/lobby camera) rather than needing
// checkin_camera_id passed explicitly on every /process-sequence call.
// Multiple cameras may be flagged at once (e.g. several entrances) — see
// CheckinService::identifyAndRecordCheckinsFromCameras(), which processes
// every flagged camera's checkin video before any zone camera runs, so
// today's daily body-fingerprint gallery is fully seeded first.
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('cameras', function (Blueprint $table) {
            $table->boolean('is_checkin')->default(false)->after('zone_id');
        });
    }

    public function down(): void
    {
        Schema::table('cameras', function (Blueprint $table) {
            $table->dropColumn('is_checkin');
        });
    }
};
