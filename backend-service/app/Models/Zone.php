<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Spatie\Activitylog\LogOptions;
use Spatie\Activitylog\Traits\LogsActivity;

class Zone extends Model
{
    use LogsActivity;

    public function getActivitylogOptions(): LogOptions
    {
        return LogOptions::defaults()
            ->useLogName('zone')
            ->logOnly(['name', 'zone_type'])
            ->logOnlyDirty()
            ->dontSubmitEmptyLogs()
            ->setDescriptionForEvent(fn(string $eventName) => match($eventName) {
                'created' => 'تم إنشاء منطقة',
                'updated' => 'تم تعديل منطقة',
                'deleted' => 'تم حذف منطقة',
                default => $eventName,
            });
    }
    
    protected $fillable = [
        'name',
        'zone_type'
    ];

    protected function serializeDate(\DateTimeInterface $date)
    {
        return $date->format('Y-m-d H:i');
    }

    protected $hidden = [
        'updated_at',
        'created_at',
    ];

    public function employees()
    {
        return $this->belongsToMany(
            Employee::class,
            'employee_zone'
        );
    }

    public function cameras()
    {
        return $this->hasMany(Camera::class);
    }
}
