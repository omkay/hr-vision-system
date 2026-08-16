<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

// Attendance checkin (see INTEGRATION-TODO-multi-photo-enrollment.md, section 2).
// One row per employee per calendar day — "was this employee present today",
// not a granular activity log. The unique(employee_id, date) constraint is
// the actual enforcement of "one checkin per day", not just app-level logic.
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('employee_checkins', function (Blueprint $table) {
            $table->id();
            $table->foreignId('employee_id')->constrained()->onDelete('cascade');
            $table->date('date');
            $table->timestamp('checked_in_at');
            $table->float('confidence');
            $table->enum('method', ['face', 'reid']);
            $table->string('photo_path')->nullable();
            $table->timestamps();

            $table->unique(['employee_id', 'date']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('employee_checkins');
    }
};
