<?php

use App\Models\Employee;
use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Str;

// Aligns the schema with resources/views/employee/profile.blade.php and
// database/seeders/EmployeeSeeder.php, both of which already reference these
// columns even though no prior migration ever created them. Without this,
// EmployeeController::showProfile() 500s with "Unknown column 'qr_token'"
// the moment GET /employee/{token} is hit, and the seeder silently drops
// every one of these fields (Eloquent ignores non-fillable keys).
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('employees', function (Blueprint $table) {
            $table->string('department')->nullable()->after('Administration');
            $table->string('direct_maneger')->nullable()->after('position');
            $table->string('work_site')->nullable()->after('end_time');
            $table->string('sheft')->nullable()->after('work_site');
            $table->string('card_id')->nullable()->unique()->after('image');
            $table->string('qr_token')->nullable()->unique()->after('card_id');
        });

        // Backfill qr_token for any employees that already exist (e.g. seeded
        // or created before this migration ran) so showProfile works for them too.
        // qr_token is intentionally NOT fillable (see Employee model), so this
        // must set the attribute directly rather than go through update()/fill().
        Employee::whereNull('qr_token')->each(function (Employee $employee) {
            $employee->qr_token = (string) Str::uuid();
            $employee->save();
        });
    }

    public function down(): void
    {
        Schema::table('employees', function (Blueprint $table) {
            $table->dropColumn(['department', 'direct_maneger', 'work_site', 'sheft', 'card_id', 'qr_token']);
        });
    }
};
