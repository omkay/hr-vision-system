<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

// Unifies checkin into the same activity_events table used by zone/events
// processing, so a single "chain of events" query (by date / employee /
// zone) covers both instead of requiring a UNION with employee_checkins.
// See CheckinController::store(), which now writes 'checkin' events here
// directly instead of creating EmployeeCheckin rows.
//
// Three changes:
// 1. event_type enum gains 'checkin'.
// 2. camera_id and vision_job_id become nullable — a checkin event has
//    neither a registered Camera row (the entrance/kiosk video isn't tied
//    to the Camera model) nor a VisionJob row (checkin runs synchronously,
//    not through the async job store zone processing uses).
// 3. New nullable columns: confidence (float) and method (face/reid) —
//    previously only tracked on employee_checkins, now general enough to
//    live on any event row (e.g. a future confidence-tracking presence
//    event could use the same column).
return new class extends Migration
{
    public function up(): void
    {
        DB::statement(
            "ALTER TABLE activity_events MODIFY event_type "
            . "ENUM('presence', 'working', 'phone_use', 'interaction', 'checkin') NOT NULL"
        );

        Schema::table('activity_events', function (Blueprint $table) {
            $table->dropForeign(['camera_id']);
            $table->dropForeign(['vision_job_id']);
        });

        DB::statement('ALTER TABLE activity_events MODIFY camera_id BIGINT UNSIGNED NULL');
        DB::statement('ALTER TABLE activity_events MODIFY vision_job_id BIGINT UNSIGNED NULL');

        Schema::table('activity_events', function (Blueprint $table) {
            $table->foreign('camera_id')->references('id')->on('cameras')->nullOnDelete();
            $table->foreign('vision_job_id')->references('id')->on('vision_jobs')->nullOnDelete();

            $table->float('confidence')->nullable()->after('employee_id');
            $table->enum('method', ['face', 'reid'])->nullable()->after('confidence');
        });
    }

    public function down(): void
    {
        Schema::table('activity_events', function (Blueprint $table) {
            $table->dropColumn(['confidence', 'method']);
            $table->dropForeign(['camera_id']);
            $table->dropForeign(['vision_job_id']);
        });

        DB::statement('ALTER TABLE activity_events MODIFY camera_id BIGINT UNSIGNED NOT NULL');
        DB::statement('ALTER TABLE activity_events MODIFY vision_job_id BIGINT UNSIGNED NOT NULL');

        Schema::table('activity_events', function (Blueprint $table) {
            $table->foreign('camera_id')->references('id')->on('cameras')->onDelete('cascade');
            $table->foreign('vision_job_id')->references('id')->on('vision_jobs')->onDelete('cascade');
        });

        DB::statement(
            "ALTER TABLE activity_events MODIFY event_type "
            . "ENUM('presence', 'working', 'phone_use', 'interaction') NOT NULL"
        );
    }
};
