<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class VisionJob extends Model
{
    protected $fillable = [
        'vision_job_id',
        'status',
        'requested_by',
        'raw_result',
        'events_persisted_count',
        'error_message',
        'finished_at',
    ];

    protected $casts = [
        'finished_at' => 'datetime',
    ];

    public function cameras()
    {
        return $this->belongsToMany(Camera::class, 'camera_vision_job');
    }

    public function events()
    {
        return $this->hasMany(ActivityEvent::class);
    }

    public function requestedBy()
    {
        return $this->belongsTo(User::class, 'requested_by');
    }
}
