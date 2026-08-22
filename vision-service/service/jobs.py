"""In-memory async job store for long-running pipeline runs."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Job:
    id: str
    status: str = "pending"      # pending | running | done | error
    progress: float = 0.0        # 0..1
    result: Any = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    # Events are appended here the instant EventEngine finalizes them —
    # well before the whole pipeline run (and therefore `result`) is ready.
    # Only ever appended to, never rewritten, so a caller polling
    # GET /events/{job_id} can safely track "how many I've already seen"
    # and only look at the tail each time (see PollVisionEventsJob on the
    # Laravel side, which does exactly that).
    partial_events: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "job_id": self.id,
            "status": self.status,
            "progress": round(self.progress, 3),
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "partial_events": list(self.partial_events),
        }


class JobStore:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex[:12])
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(self, fn, *args, **kwargs) -> Job:
        job = self.create()

        def _run():
            job.status = "running"
            job.started_at = time.time()
            try:
                def _progress(done, total):
                    job.progress = done / max(total, 1)
                def _on_event(ev: dict):
                    # list.append is atomic under the GIL, so no extra lock
                    # is needed here even though this runs on the worker
                    # thread while GET /events/{job_id} reads the list
                    # concurrently from a request-handling thread.
                    job.partial_events.append(ev)
                kwargs["progress"] = _progress
                kwargs["on_event"] = _on_event
                job.result = fn(*args, **kwargs)
                job.status = "done"
                job.progress = 1.0
            except Exception as e:
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            finally:
                job.finished_at = time.time()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return job


store = JobStore()
