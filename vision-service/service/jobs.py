"""In-memory async job store for long-running pipeline runs."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


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
                kwargs["progress"] = _progress
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
