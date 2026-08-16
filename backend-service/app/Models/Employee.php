<?php

namespace App\Models;

use Carbon\Carbon;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\SoftDeletes;
use Illuminate\Support\Str;
use Spatie\Activitylog\LogOptions;
use Spatie\Activitylog\Traits\LogsActivity;

class Employee extends Model
{
    use SoftDeletes, LogsActivity;

    public function getActivitylogOptions(): LogOptions
    {
        return LogOptions::defaults()
            ->useLogName('employee')
            ->logOnly([
                'name',
                'Administration',
                'department',
                'job_num',
                'position',
                'direct_maneger',
                'start_date',
                'start_time',
                'end_time',
                'work_site',
                'sheft',
                'phone_num',
                'image',
                'card_id'
            ])
            ->logOnlyDirty()
            ->dontSubmitEmptyLogs()
            ->setDescriptionForEvent(fn(string $eventName) => match($eventName) {
                'created' => 'تم إنشاء موظف',
                'updated' => 'تم تعديل بيانات موظف',
                'deleted' => 'تم حذف موظف',
                default => $eventName,
            });
    }

    protected $fillable = [
        'name',
        'Administration',
        'department',
        'job_num',
        'position',
        'direct_maneger',
        'start_date',
        'start_time',
        'end_time',
        'work_site',
        'sheft',
        'phone_num',
        'image',
        'card_id'
        // qr_token is intentionally not fillable — it's system-generated,
        // see the static::creating() hook below, never set from a request.
    ];

    protected $hidden = [
        'created_at',
        'updated_at',
        'deleted_at'
    ];

    protected static function booted(): void
    {
        static::creating(function (Employee $employee) {
            $employee->qr_token = (string) Str::uuid();
        });
    }

    public function setStartDateAttribute($value)
    {
        if (!$value) {
            $this->attributes['start_date'] = null;
            return;
        }

        try {
            // إذا جاي رقم من Excel
            if (is_numeric($value)) {
                $this->attributes['start_date'] =
                    Carbon::createFromTimestamp(($value - 25569) * 86400)->format('Y-m-d');
                return;
            }

            // فورمات محتملة
            $formats = [
                'd-m-Y',
                'd/m/Y',
                'Y-m-d',
                'm/d/Y',
                'd-m-y',
                'd/m/y',
            ];

            foreach ($formats as $format) {
                try {
                    $this->attributes['start_date'] =
                        Carbon::createFromFormat($format, $value)->format('Y-m-d');
                    return;
                } catch (\Exception $e) {}
            }

            // fallback
            $this->attributes['start_date'] =
                Carbon::parse($value)->format('Y-m-d');

        } catch (\Exception $e) {
            $this->attributes['start_date'] = null;
        }
    }

    protected function serializeDate(\DateTimeInterface $date)
    {
        return $date->format('Y-m-d H:i');
    }

    public function zones()
    {
        return $this->belongsToMany(
            Zone::class,
            'employee_zone'
        );
    }

    public function photos()
    {
        return $this->hasMany(EmployeePhoto::class);
    }

    public function faceImages()
    {
        return $this->photos()->where('type', 'face');
    }

    public function bodyImages()
    {
        return $this->photos()->where('type', 'body');
    }

    public function checkins()
    {
        return $this->hasMany(EmployeeCheckin::class);
    }
}
