<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Camera extends Model
{
    protected $fillable = [
        'name',
        'video',
        'zone_id',
        'is_checkin',
    ];

    protected $casts = [
        // 'integer' (not 'boolean') so API responses serialize this as 1/0
        // rather than true/false — that's the shape consumers of this field
        // (e.g. the desktop admin app) expect.
        'is_checkin' => 'integer',
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