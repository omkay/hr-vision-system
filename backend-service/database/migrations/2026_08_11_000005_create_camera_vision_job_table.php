<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('camera_vision_job', function (Blueprint $table) {
            $table->id();
            $table->foreignId('camera_id')->constrained()->onDelete('cascade');
            $table->foreignId('vision_job_id')->constrained('vision_jobs')->onDelete('cascade');
            $table->timestamps();

            $table->unique(['camera_id', 'vision_job_id']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('camera_vision_job');
    }
};
