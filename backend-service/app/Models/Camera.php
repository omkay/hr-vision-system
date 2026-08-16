<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Camera extends Model
{
    protected $fillable = [
        'name',
        'video',
        'zone_id',
    ];

    protected $hidden = [
        'created_at',
        'updated_at',
    ];

    public function zone()
    {
        return $this->belongsTo(Zone::class);
    }

    public function visionJobs()
    {
        return $this->belongsToMany(VisionJob::class, 'camera_vision_job');
    }

    public function events()
    {
        return $this->hasMany(ActivityEvent::class);
    }
}