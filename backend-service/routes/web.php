<?php

use App\Http\Controllers\EmployeeController;
use Illuminate\Support\Facades\Route;


Route::get('/employee/{token}', [EmployeeController::class, 'showProfile']);

Route::get('/login', function () {
    return view('web app.login');
})->name('login_page');

Route::get('/dashboard', function () {
    return view('web app.dashboard');
})->name('dashboard');

Route::get('/nfc-scanner', function () {
    return view('web app.nfc_scanner');
})->name('nfc-scanner');

Route::get('/', function () {
    return redirect()->route('login_page');
});