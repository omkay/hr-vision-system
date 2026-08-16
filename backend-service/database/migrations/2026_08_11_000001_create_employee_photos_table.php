<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

// Multi-photo enrollment (see INTEGRATION-TODO-multi-photo-enrollment.md, section 1).
// employees.image stays as the single "primary photo" shown in the UI list;
// this table holds every photo actually sent to the vision service for
// enrollment, since averaging more face images and stacking more body images
// meaningfully improves match quality on the vision side.
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('employee_photos', function (Blueprint $table) {
            $table->id();
            $table->foreignId('employee_id')->constrained()->onDelete('cascade');
            $table->string('path');
            $table->enum('type', ['face', 'body']);
            $table->timestamps();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('employee_photos');
    }
};
