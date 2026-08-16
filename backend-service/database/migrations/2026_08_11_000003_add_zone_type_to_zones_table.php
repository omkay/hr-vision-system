<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

// A zone is always the full video frame — no sub-regions to draw or store
// coordinates for (see INTEGRATION-TODO-multi-photo-enrollment.md, section 3).
// zone_type maps directly onto the vision service's ZoneDefinition.zone_type:
// work_area -> presence/working/phone_use, common_area -> presence/interaction.
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('zones', function (Blueprint $table) {
            $table->enum('zone_type', ['work_area', 'common_area'])->default('work_area')->after('name');
        });
    }

    public function down(): void
    {
        Schema::table('zones', function (Blueprint $table) {
            $table->dropColumn('zone_type');
        });
    }
};
