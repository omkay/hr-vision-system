<?php

namespace App\Jobs;

use App\Models\Employee;
use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;
use Illuminate\Queue\SerializesModels;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;

/**
 * Registers (or re-registers) an employee's face in the vision service's
 * identity gallery, so the activity-tracking pipeline can recognise them.
 *
 * `job_num` is used as the vision-side identity key — it's already unique
 * and human-meaningful, no separate "vision_key" column needed (see ADR-001).
 * Re-enrolling the same job_num replaces that employee's stored embeddings,
 * so this job is safe to dispatch again whenever the photo changes.
 */
class EnrollEmployeeInVisionService implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, Queueable, SerializesModels;

    public int $tries = 5;

    public array $backoff = [10, 30, 60, 120, 300];

    public function __construct(public int $employeeId)
    {
    }

    public function handle(): void
    {
        $employee = Employee::find($this->employeeId);

        if (! $employee) {
            return;
        }

        $baseUrl = rtrim(config('app.internal_url'), '/') . '/storage/';
        $faceUrls = $employee->faceImages()->pluck('path')->map(fn ($path) => $baseUrl . $path)->all();
        $bodyUrls = $employee->bodyImages()->pluck('path')->map(fn ($path) => $baseUrl . $path)->all();

        // Backward compatible fallback: employees enrolled before multi-photo
        // support (or who only ever had the single profile photo) have no
        // employee_photos rows yet — reuse the primary image for both, same
        // as the original single-photo behavior.
        if (empty($faceUrls) && empty($bodyUrls)) {
            if (! $employee->image) {
                return;
            }
            $faceUrls = [$baseUrl . $employee->image];
            $bodyUrls = [$baseUrl . $employee->image];
        }

        $visionUrl = rtrim(config('services.vision.url'), '/') . '/enroll';

        $response = Http::timeout(30)->post($visionUrl, [
            'name' => (string) $employee->job_num,
            'face_images' => $faceUrls,
            'body_images' => $bodyUrls,
        ]);

        if ($response->failed()) {
            Log::warning('Vision enrollment failed', [
                'employee_id' => $employee->id,
                'job_num' => $employee->job_num,
                'status' => $response->status(),
                'body' => $response->body(),
            ]);

            // Throwing (rather than just logging) lets the queue's retry/backoff
            // handle transient failures (vision service mid-restart, cold model
            // load, etc.) instead of silently dropping the enrollment.
            $response->throw();
        }

        Log::info('Vision enrollment succeeded', [
            'employee_id' => $employee->id,
            'job_num' => $employee->job_num,
        ]);
    }

    public function failed(\Throwable $exception): void
    {
        Log::error('Vision enrollment gave up after retries', [
            'employee_id' => $this->employeeId,
            'error' => $exception->getMessage(),
        ]);
    }
}
