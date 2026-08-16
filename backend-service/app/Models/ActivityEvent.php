<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class ActivityEvent extends Model
{
    protected $fillable = [
        'camera_id',
        'vision_job_id',
        'employee_id',
        'event_type',
        'start_s',
        'end_s',
        'duration_s',
        'zone',
        'zone_type',
        'work_proxy',
        'peers',
    ];

    protected $casts = [
        'peers' => 'array',
    ];

    public function camera()
    {
        return $this->belongsTo(Camera::class);
    }

    public function employee()
    {
        return $this->belongsTo(Employee::class);
    }

    public function visionJob()
    {
        return $this->belongsTo(VisionJob::class);
    }
}
