<?php

namespace App\Jobs;

use App\Models\ActivityEvent;
use App\Models\Employee;
use App\Models\VisionJob;
use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;
use Illuminate\Queue\SerializesModels;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;

/**
 * Polls GET /events/{job_id} until the vision service reports done or error,
 * then persists the results — its job store is in-memory only, so this is
 * the only copy of the results that survives a vision-service restart.
 * See INTEGRATION-TODO-multi-photo-enrollment.md section 3.
 *
 * Chosen over a webhook callback so nothing new has to call into
 * Hr_SmartPay from outside — this reuses the same queue worker enrollment
 * already relies on. Retries on a backoff since /events/run can take
 * anywhere from seconds to many minutes depending on video length.
 */
class PollVisionEventsJob implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, Queueable, SerializesModels;

    public int $tries = 30;

    // Laravel reuses the last value for any attempt beyond the array length,
    // so this settles into a 2-minute poll interval for long-running jobs.
    public array $backoff = [10, 20, 30, 60, 120];

    public function __construct(public int $visionJobId)
    {
    }

    public function handle(): void
    {
        $visionJob = VisionJob::find($this->visionJobId);

        if (! $visionJob) {
            return;
        }

        $url = rtrim(config('services.vision.url'), '/') . '/events/' . $visionJob->vision_job_id;
        $response = Http::timeout(30)->get($url);

        if ($response->failed()) {
            Log::warning('PollVisionEventsJob: status check failed', [
                'vision_job_id' => $visionJob->vision_job_id,
                'status' => $response->status(),
            ]);

            // Transient — let the queue's retry/backoff handle it rather
            // than giving up on the first failed poll.
            $response->throw();
        }

        $body = $response->json();
        $status = $body['status'] ?? null;

        if ($status === 'done') {
            $this->persistResults($visionJob, $body['result'] ?? []);
            return;
        }

        if ($status === 'error') {
            $visionJob->update([
                'status' => 'error',
                'error_message' => $body['error'] ?? 'Unknown vision-service error',
                'finished_at' => now(),
            ]);
            return;
        }

        // Still pending/running — mark it and try again on the next backoff tick.
        $visionJob->update(['status' => $status ?? $visionJob->status]);
        throw new \RuntimeException("Vision job {$visionJob->vision_job_id} still {$status}, retrying");
    }

    private function persistResults(VisionJob $visionJob, array $result): void
    {
        $cameraIds = $visionJob->cameras()->pluck('cameras.id')->all();

        foreach (($result['events'] ?? []) as $event) {
            $cameraId = (int) ($event['camera_id'] ?? 0);

            // Defensive: only persist events for cameras actually linked to
            // this job, in case the vision service echoes back something unexpected.
            if (! in_array($cameraId, $cameraIds, true)) {
                continue;
            }

            $employeeId = null;
            if (($event['employee_id'] ?? 'UNKNOWN') !== 'UNKNOWN') {
                $employeeId = Employee::where('job_num', $event['employee_id'])->value('id');
            }

            ActivityEvent::create([
                'camera_id' => $cameraId,
                'vision_job_id' => $visionJob->id,
                'employee_id' => $employeeId,
                'event_type' => $event['event_type'],
                'start_s' => $event['start_s'],
                'end_s' => $event['end_s'],
                'duration_s' => $event['duration_s'],
                'zone' => $event['zone'] ?? null,
                'zone_type' => $event['zone_type'] ?? null,
                'work_proxy' => $event['work_proxy'] ?? null,
                'peers' => $event['peers'] ?? null,
            ]);
        }

        $visionJob->update([
            'status' => 'done',
            'raw_result' => json_encode($result),
            'finished_at' => now(),
        ]);
    }

    public function failed(\Throwable $exception): void
    {
        $visionJob = VisionJob::find($this->visionJobId);

        if ($visionJob && $visionJob->status !== 'done') {
            $visionJob->update([
                'status' => 'error',
                'error_message' => 'Gave up polling: ' . $exception->getMessage(),
                'finished_at' => now(),
            ]);
        }
    }
}
