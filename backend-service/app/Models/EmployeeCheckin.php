<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class EmployeeCheckin extends Model
{
    protected $fillable = [
        'employee_id',
        'date',
        'checked_in_at',
        'confidence',
        'method',
        'photo_path',
    ];

    protected $casts = [
        'date' => 'date:Y-m-d',
        'checked_in_at' => 'datetime',
    ];

    public function employee()
    {
        return $this->belongsTo(Employee::class);
    }
}
