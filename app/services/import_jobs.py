from dataclasses import dataclass, field
from threading import Lock, Thread
from typing import Callable
from uuid import uuid4


@dataclass
class ImportJob:
    id: str
    total: int
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    current_company: str = ""
    status: str = "queued"
    result_ids: list[int] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock, repr=False)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "id": self.id,
                "total": self.total,
                "processed": self.processed,
                "succeeded": self.succeeded,
                "failed": self.failed,
                "current_company": self.current_company,
                "status": self.status,
                "result_ids": list(self.result_ids),
                "failures": list(self.failures),
                "percent": round((self.processed / self.total) * 100) if self.total else 0,
            }


class ImportJobManager:
    def __init__(self):
        self.jobs: dict[str, ImportJob] = {}
        self.lock = Lock()

    def create(self, total: int) -> ImportJob:
        job = ImportJob(id=uuid4().hex, total=total)
        with self.lock:
            self.jobs[job.id] = job
        return job

    def get(self, job_id: str) -> ImportJob | None:
        with self.lock:
            return self.jobs.get(job_id)

    def start(self, job: ImportJob, worker: Callable[[ImportJob], None]) -> None:
        Thread(target=worker, args=(job,), daemon=True).start()

