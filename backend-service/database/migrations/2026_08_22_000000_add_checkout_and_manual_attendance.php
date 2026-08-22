<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

// Attendance needs a departure time, and it needs to be correctable by hand.
//
// 1. event_type gains 'checkout'. Reports can then answer "when did X arrive
//    and when did they leave" from one table, the same way 'checkin' was
//    unified in 2026_08_18.
//
// 2. method gains 'manual'. Recognition genuinely fails on real people — a
//    facial injury, a big change in appearance — and when it does, HR has to
//    be able to record attendance by hand. Keeping the manual entries in the
//    same table as the automatic ones (rather than a parallel table) means
//    every report and export picks them up for free; `method` is what
//    distinguishes them, so a reviewer can always tell which times the
//    system observed and which a person asserted.
//
// 3. recorded_by identifies the user who made a manual entry. Attendance
//    feeds payroll, so "who typed this" has to be answerable later. Null for
//    the automatic rows the vision pipeline writes.
return new class extends Migration
{
    public function up(): void
    {
        DB::statement(
            "ALTER TABLE activity_events MODIFY event_type "
            . "ENUM('presence', 'working', 'phone_use', 'interaction', 'checkin', 'checkout') NOT NULL"
        );

        DB::statement(
            "ALTER TABLE activity_events MODIFY method "
            . "ENUM('face', 'reid', 'manual') NULL"
        );

        Schema::table('activity_events', function (Blueprint $table) {
            $table->foreignId('recorded_by')->nullable()->after('method')
                ->constrained('users')->nullOnDelete();
            // Attendance screens query "this employee's checkin/checkout on
            // this date" per employee per day; without this the daily view is
            // a full scan of every event ever recorded.
            $table->index(['event_type', 'created_at'], 'activity_events_type_created_idx');
        });
    }

    public function down(): void
    {
        Schema::table('activity_events', function (Blueprint $table) {
            $table->dropIndex('activity_events_type_created_idx');
            $table->dropConstrainedForeignId('recorded_by');
        });

        // Manual and checkout rows can't be represented by the old enums, so
        // drop them rather than letting the ALTER coerce them to ''.
        DB::table('activity_events')->where('event_type', 'checkout')->delete();
        DB::table('activity_events')->where('method', 'manual')->update(['method' => null]);

        DB::statement(
            "ALTER TABLE activity_events MODIFY method ENUM('face', 'reid') NULL"
        );
        DB::statement(
            "ALTER TABLE activity_events MODIFY event_type "
            . "ENUM('presence', 'working', 'phone_use', 'interaction', 'checkin') NOT NULL"
        );
    }
};
