<?php

namespace App\Console\Commands;

use Illuminate\Console\Command;
use Laravel\Sanctum\PersonalAccessToken;
use Carbon\Carbon;

class DeleteExpiredTokens extends Command
{
    protected $signature = 'app:delete-expired-tokens';

    protected $description = 'Delete expired Sanctum tokens based on inactivity';

    public function handle()
    {
        // نحسب الوقت (قبل ساعة)
        $threshold = now()->subHour();

        // حذف التوكنات اللي:
        // last_used_at أقدم من ساعة
        // أو last_used_at = null و created_at أقدم من ساعة
        $deleted = PersonalAccessToken::where(function ($query) use ($threshold) {
            $query->whereNotNull('last_used_at')
                  ->where('last_used_at', '<', $threshold);
        })->orWhere(function ($query) use ($threshold) {
            $query->whereNull('last_used_at')
                  ->where('created_at', '<', $threshold);
        })->delete();

        $this->info("Deleted {$deleted} expired tokens.");

        return Command::SUCCESS;
    }
}