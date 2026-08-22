<?php

namespace Database\Seeders;

use App\Models\User;
use Illuminate\Database\Console\Seeds\WithoutModelEvents;
use Illuminate\Database\Seeder;

class UserSeeder extends Seeder
{
    /**
     * Run the database seeds.
     */
    public function run(): void
    {
        // role_id 3 is the CEO role (see RoleSeeder) — the username now
        // matches it rather than describing the department that created it.
        User::create([
            'user_name' => 'ceo',
            'password' => 'It123#321',
            'role_id' => 3
        ]);
    }
}
