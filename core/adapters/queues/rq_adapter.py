import uuid
from typing import Any

from core.config import get_settings


class RQJobQueue:
    def __init__(self) -> None:
        import redis
        from rq import Queue

        settings = get_settings()
        self._redis = redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)
        self._queue = Queue("rag-jobs", connection=self._redis)

    def enqueue(self, job_type: str, payload: dict[str, Any]) -> str:
        from worker.rq_worker import run_job

        job_id = str(uuid.uuid4())
        if job_type == "ingest":
            import hashlib
            import json
            from pathlib import Path

            from rq.job import Job

            identity = json.dumps(
                [
                    get_settings().qdrant_collection,
                    payload["use_case"],
                    payload["source_id"],
                    payload["checksum"],
                ]
            )
            job_id = "ingest-" + hashlib.sha256(identity.encode()).hexdigest()
            with self._redis.lock("rag:enqueue:" + job_id, timeout=30, blocking_timeout=10):
                if Job.exists(job_id, connection=self._redis):
                    previous = Job.fetch(job_id, connection=self._redis)
                    source_key = hashlib.sha256(
                        json.dumps([payload["use_case"], payload["source_id"]]).encode()
                    ).hexdigest()
                    current = self._redis.get(
                        "rag:revision:" + get_settings().qdrant_collection + ":" + source_key
                    )
                    state = previous.get_status()
                    if state in {"queued", "deferred", "scheduled", "started"} or (
                        state == "finished" and current == payload["checksum"].encode()
                    ):
                        Path(payload["file_path"]).unlink(missing_ok=True)
                        return job_id
                    previous.delete()
                self._queue.enqueue(
                    run_job,
                    job_type,
                    payload,
                    job_id=job_id,
                    description=f"{job_type} task",
                    job_timeout=1800,
                    result_ttl=86400,
                    failure_ttl=86400,
                )
        else:
            self._queue.enqueue(
                run_job,
                job_type,
                payload,
                job_id=job_id,
                description=f"{job_type} task",
                job_timeout=1800,
                result_ttl=86400,
                failure_ttl=86400,
            )
        return job_id

    def get_status(self, job_id: str) -> dict[str, Any]:
        from rq.job import Job

        job = Job.fetch(job_id, connection=self._redis)
        status_map = {
            "queued": "queued",
            "deferred": "queued",
            "scheduled": "queued",
            "started": "running",
            "finished": "succeeded",
            "failed": "failed",
            "stopped": "failed",
            "canceled": "failed",
        }
        return {
            "job_id": job_id,
            "status": status_map.get(job.get_status(), "queued"),
            "result": job.return_value(),
            "progress": job.meta.get("progress"),
            "error": {"code": "job_failed", "message": "Job failed; contact the administrator"}
            if job.get_status() in {"failed", "stopped", "canceled"}
            else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "completed_at": job.ended_at.isoformat() if job.ended_at else None,
        }
