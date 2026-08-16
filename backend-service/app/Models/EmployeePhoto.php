<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class EmployeePhoto extends Model
{
    protected $fillable = [
        'employee_id',
        'path',
        'type',
    ];

    public function employee()
    {
        return $this->belongsTo(Employee::class);
    }
}
