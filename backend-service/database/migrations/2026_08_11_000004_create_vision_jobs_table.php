<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

// Zone-based activity tracking (see INTEGRATION-TODO-multi-photo-enrollment.md,
// section 3). Job-centric rather than camera-centric, since /events/run
// accepts a batch of videos in one call — a single vision_job can cover
// several cameras at once (see camera_vision_job pivot).
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('vision_jobs', function (Blueprint $table) {
            $table->id();
            $table->string('vision_job_id')->unique(); // the job_id returned by POST /events/run
            $table->enum('status', ['queued', 'running', 'done', 'error'])->default('queued');
            $table->foreignId('requested_by')->nullable()->constrained('users')->nullOnDelete();
            $table->longText('raw_result')->nullable(); // full JobResultPayload JSON, once done — cheap debugging insurance
            $table->text('error_message')->nullable();
            $table->timestamp('finished_at')->nullable();
            $table->timestamps();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('vision_jobs');
    }
};
