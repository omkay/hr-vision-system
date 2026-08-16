<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

// Persists vision-service events once a job finishes, since its own job
// store is in-memory only and results vanish on a vision-service restart.
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('activity_events', function (Blueprint $table) {
            $table->id();
            $table->foreignId('camera_id')->constrained()->onDelete('cascade');
            $table->foreignId('vision_job_id')->constrained('vision_jobs')->onDelete('cascade');
            $table->foreignId('employee_id')->nullable()->constrained()->nullOnDelete(); // null when the vision service returned UNKNOWN
            $table->enum('event_type', ['presence', 'working', 'phone_use', 'interaction']);
            $table->float('start_s');
            $table->float('end_s');
            $table->float('duration_s');
            $table->string('zone')->nullable();
            $table->enum('zone_type', ['work_area', 'common_area'])->nullable();
            $table->string('work_proxy')->nullable(); // e.g. "laptop+monitor", only for 'working' events
            $table->json('peers')->nullable(); // two employee identifiers, only for 'interaction' events
            $table->timestamps();

            $table->index(['camera_id', 'employee_id']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('activity_events');
    }
};
