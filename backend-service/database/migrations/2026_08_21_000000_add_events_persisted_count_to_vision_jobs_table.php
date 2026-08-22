<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('vision_jobs', function (Blueprint $table) {
            // Cursor into vision-service's `partial_events` list (which only
            // ever grows, never rewrites earlier entries) — lets
            // PollVisionEventsJob persist just the newly-finalized events on
            // each poll instead of re-inserting everything it already saw.
            // See PollVisionEventsJob::persistNewPartialEvents().
            $table->unsignedInteger('events_persisted_count')->default(0)->after('raw_result');
        });
    }

    public function down(): void
    {
        Schema::table('vision_jobs', function (Blueprint $table) {
            $table->dropColumn('events_persisted_count');
        });
    }
};
