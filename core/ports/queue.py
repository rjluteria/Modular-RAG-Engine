"""JobQueue contract shared by local RQ and Azure Service Bus adapters."""

from typing import Any, Protocol


class JobQueue(Protocol):
    def enqueue(self, job_type: str, payload: dict[str, Any]) -> str:
        """Enqueues a job, returns a job_id."""
        ...

    def get_status(self, job_id: str) -> dict[str, Any]:
        """Returns {status, result, error, created_at, completed_at}."""
        ...
