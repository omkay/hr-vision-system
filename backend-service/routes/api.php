<?php

use App\Http\Controllers\CameraController;
use App\Http\Controllers\CameraProcessingController;
use App\Http\Controllers\CheckinController;
use App\Http\Controllers\EmployeeController;
use App\Http\Controllers\EmployeePhotoController;
use App\Http\Controllers\LogController;
use App\Http\Controllers\ReportController;
use App\Http\Controllers\RoleController;
use App\Http\Controllers\ZoneController;
use App\Http\Controllers\UserController;
use Illuminate\Support\Facades\Route;

Route::post('/login', [UserController::class, 'login'])->middleware('filter_input', 'throttle:login');

Route::middleware(['filter_input', 'auth:sanctum', 'check.idel', 'authorization:HR Manager,CEO,HR Employee'])->group(function () {
    Route::get('/logout', [UserController::class, 'logout']);

    Route::post('/zone/add', [ZoneController::class, 'store']);
    Route::get('/zone/get', [ZoneController::class, 'get_zones']);

    Route::post('/camera/add', [CameraController::class, 'add_camera']);
    Route::get('/camera/get', [CameraController::class, 'get_cameras']);
    Route::post('/camera/update/{id}', [CameraController::class, 'update_camera']);
    Route::delete('/camera/delete/{id}', [CameraController::class, 'delete_camera']);

    Route::post('/camera/{id}/process', [CameraProcessingController::class, 'process']);
    Route::post('/cameras/process-batch', [CameraProcessingController::class, 'processBatch']);
    // Full daily flow in one call: checkin video first (seeds that day's
    // body-fingerprint gallery in vision-service), then the given zone
    // cameras processed against that same day. See processSequence().
    Route::post('/process-sequence', [CameraProcessingController::class, 'processSequence']);
    Route::get('/camera/{id}/events', [CameraProcessingController::class, 'events']);
    Route::get('/events', [CameraProcessingController::class, 'allEvents']);
    Route::get('/vision-jobs/{id}', [CameraProcessingController::class, 'jobStatus']);
    Route::get('/reports/summary', [ReportController::class, 'summary']);

    Route::post('/employees/add', [EmployeeController::class, 'store']);
    Route::get('/employees/get', [EmployeeController::class, 'get_employees']);

    Route::post('/employee/zone/sync', [ZoneController::class, 'sync']);

    Route::get('/employees/{id}/photos', [EmployeePhotoController::class, 'index']);
    Route::post('/employees/{id}/photos', [EmployeePhotoController::class, 'store']);
    Route::delete('/employees/{id}/photos/{photoId}', [EmployeePhotoController::class, 'destroy']);

    // A shared kiosk device logs in with one HR-issued account and stays
    // logged in — auth:sanctum still applies, it's just not per-employee,
    // since employees have no login accounts of their own in this app.
    Route::post('/checkin', [CheckinController::class, 'store']);
    Route::get('/checkins', [CheckinController::class, 'index']);
});

Route::middleware(['filter_input', 'auth:sanctum', 'check.idel', 'authorization:HR Manager,CEO'])->group(function () {

    Route::put('/zone/update/{id}', [ZoneController::class, 'update']);
    Route::delete('/zone/delete/{id}', [ZoneController::class, 'destroy']);

    Route::post('/user/register', [UserController::class, 'register']);
    Route::get('/user/get', [UserController::class, 'getUseres']);
    Route::delete('/user/delete/{id}', [UserController::class, 'destroy']);
    Route::put('/user/update/{id}', [UserController::class, 'update']);

    Route::post('/employees/update/{id}', [EmployeeController::class, 'update']);
    Route::delete('/employees/delete/{id}', [EmployeeController::class, 'destroy']);
});


Route::middleware(['filter_input', 'auth:sanctum', 'check.idel', 'authorization:CEO'])->group(function () {
    Route::get('/logs', [LogController::class, 'index']);
    Route::put('/user/reset/{id}', [UserController::class, 'reset_pass']);

    // Role management is CEO-only — same sensitivity tier as password resets and logs,
    // since a role controls what every other route lets a user do (see Authorization middleware).
    Route::post('/role/add', [RoleController::class, 'store']);
    Route::put('/role/update/{id}', [RoleController::class, 'update']);
    Route::delete('/role/delete/{id}', [RoleController::class, 'destroy']);
});
