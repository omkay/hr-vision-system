<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     */
    public function up(): void
    {
        Schema::create('employees', function (Blueprint $table) {
            $table->id();
            $table->string('name'); 
            $table->string('Administration'); 
            $table->integer('job_num'); 
            $table->string('position'); 
            $table->date('start_date'); 
            $table->time('start_time'); 
            $table->time('end_time'); 
            $table->string('phone_num')->nullable();
            $table->string('image')->nullable();
            $table->softDeletes();
            $table->timestamps();

            $table->unique(['job_num', 'deleted_at']);
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('employees');
    }
};
