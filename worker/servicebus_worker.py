"""Replay persisted outbox references; execute each job under a renewable lease."""

import logging
from datetime import UTC, datetime

from core.adapters.queues.service_bus_adapter import service_bus_client
from core.adapters.state.blob_state import blob_state
from core.config import get_settings
from worker.rq_worker import run_job

logger = logging.getLogger("modular_rag.worker")


def process_job(job_id: str) -> None:
    if len(job_id) != 64 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("Invalid job reference")
    with blob_state() as state, state.lock("jobs/" + job_id):
        row = state.read("jobs/" + job_id)
        if not row or "job_type" not in row:
            return
        if row["status"] not in {"succeeded", "failed"}:
            row["status"] = "running"
            state.write("jobs/" + job_id, row)
            try:
                row["result"] = run_job(row["job_type"], row["payload"])
                row["status"] = "succeeded"
            except Exception as error:  # noqa: BLE001 -- sanitize job/broker boundaries
                logger.error("Job failed (%s)", type(error).__name__)
                row["status"] = "failed"
                row["error"] = {"code": "job_failed", "message": "Job failed; contact the administrator"}
            row["payload"] = {}  # Do not retain completed input or queries as history.
            row["completed_at"] = datetime.now(UTC).isoformat()
            state.write("jobs/" + job_id, row)
    with blob_state() as state, state.lock("outbox"):
        pending = state.read("outbox", {})
        pending.pop(job_id, None)
        state.write("outbox", pending)


def _drain() -> None:
    settings = get_settings()
    from core.adapters.originals.blob_originals import expire_originals
    expire_originals()
    from worker.expiry import expire_jobs
    expire_jobs()
    # The durable outbox recovers crashes between enqueue and broker delivery.
    with blob_state() as state:
        pending = list(state.read("outbox", {}))[:settings.max_queued_jobs]
    for job_id in pending:
        process_job(job_id)
    with service_bus_client() as client, client.get_queue_receiver(
        settings.azure_service_bus_queue, max_wait_time=5
    ) as receiver:
        for message in receiver:
            try:
                process_job(str(message))
                receiver.complete_message(message)
            except ValueError:
                receiver.dead_letter_message(message, reason="invalid_reference")
            except Exception as error:  # noqa: BLE001 -- sanitize job/broker boundaries
                logger.error("Worker unavailable (%s)", type(error).__name__)
                receiver.abandon_message(message)
                raise RuntimeError("Worker dependency unavailable") from None


def main() -> None:
    from azure.core.exceptions import HttpResponseError

    acquired = False
    try:
        with blob_state() as state, state.lock("worker-execution", wait=0):
            acquired = True
            _drain()
    except HttpResponseError as error:
        if acquired or error.status_code != 409:
            logger.error("Worker coordination unavailable (%s)", type(error).__name__)
            raise SystemExit(1) from None
    except Exception as error:  # noqa: BLE001 -- do not expose service responses in process logs
        logger.error("Worker unavailable (%s)", type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
