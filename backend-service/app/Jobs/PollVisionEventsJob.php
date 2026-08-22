<?php

namespace App\Jobs;

use App\Models\ActivityEvent;
use App\Models\Employee;
use App\Models\VisionJob;
use App\Services\AttendanceService;
use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;
use Illuminate\Queue\SerializesModels;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;

/**
 * Polls GET /events/{job_id} until the vision service reports done or error.
 * Its job store is in-memory only, so this is the only copy of the results
 * that survives a vision-service restart. See
 * INTEGRATION-TODO-multi-photo-enrollment.md section 3.
 *
 * Chosen over a webhook callback so nothing new has to call into
 * Hr_SmartPay from outside — this reuses the same queue worker enrollment
 * already relies on.
 *
 * Events are persisted incrementally on every poll (not just once the job
 * finishes) — vision-service's `partial_events` grows live as EventEngine
 * finalizes each event mid-video (see events_engine.py's on_event callback),
 * so dashboard.html/events.html start showing activity for a long-running
 * job well before it's done, instead of only once the whole video sequence
 * completes. `events_persisted_count` is a cursor into that list so each
 * poll only inserts the tail it hasn't seen yet.
 */
class PollVisionEventsJob implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, Queueable, SerializesModels;

    // Time-boxed rather than try-count-boxed: a fixed, short backoff would
    // need an impractically large $tries to cover a long multi-camera
    // sequence, so this caps on wall-clock time instead and gives up after
    // 2 hours of a job never reaching done/error.
    public int $tries = 2000;
    public int $backoff = 15;

    public function __construct(public int $visionJobId)
    {
    }

    public function retryUntil(): \DateTime
    {
        return now()->addHours(2);
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

        // Persist newly-finalized events on every poll, regardless of
        // status — including 'running', which is the whole point: activity
        // shows up while the job is still going, not just once it's done.
        $this->persistNewPartialEvents($visionJob, $body['partial_events'] ?? []);

        if ($status === 'done') {
            $visionJob->update([
                'status' => 'done',
                'raw_result' => json_encode($body['result'] ?? []),
                'finished_at' => now(),
            ]);

            // Derive checkouts now that every sighting for the day is
            // persisted — a checkout is (last sighting + grace), so it can
            // only be computed once there are no more sightings coming.
            // Idempotent, and never overwrites a manual checkout, so a
            // second job on the same day is harmless. Failure here must not
            // fail the job: the events are already saved, and HR can
            // re-derive from the attendance screen.
            try {
                app(AttendanceService::class)->deriveCheckouts(now()->toDateString());
            } catch (\Throwable $e) {
                Log::warning('Checkout derivation failed after job completion', [
                    'vision_job_id' => $visionJob->id,
                    'error' => $e->getMessage(),
                ]);
            }
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

    /**
     * Persists ActivityEvent rows for every entry in $partialEvents past
     * the `events_persisted_count` cursor. Safe to call on every poll:
     * vision-service's partial_events list is append-only (see jobs.py's
     * Job.partial_events), so the same prefix is never re-sent, and by the
     * time status flips to 'done' this list already equals result.events
     * in content — so nothing further needs to be (or should be) persisted
     * from `result` itself.
     */
    private function persistNewPartialEvents(VisionJob $visionJob, array $partialEvents): void
    {
        $alreadyPersisted = $visionJob->events_persisted_count;
        $newEvents = array_slice($partialEvents, $alreadyPersisted);

        if (empty($newEvents)) {
            return;
        }

        $cameraIds = $visionJob->cameras()->pluck('cameras.id')->all();

        foreach ($newEvents as $event) {
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

        // Advance the cursor past every entry we just looked at (not just
        // the ones that passed the camera_id check) — those filtered-out
        // ones are deterministic given the same event, so there's no
        // benefit to re-checking them on the next poll.
        $visionJob->update(['events_persisted_count' => count($partialEvents)]);
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
