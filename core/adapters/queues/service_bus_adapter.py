"""Durable job outbox; broker messages contain opaque job references only."""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from rq.exceptions import NoSuchJobError

from core.adapters.state.blob_state import blob_state
from core.config import get_settings


def service_bus_client():
    settings = get_settings()
    if settings.azure_service_bus_connection_string:
        return ServiceBusClient.from_connection_string(settings.azure_service_bus_connection_string)
    if not settings.azure_service_bus_namespace:
        raise ValueError("Selected queue adapter requires configuration")
    return ServiceBusClient(settings.azure_service_bus_namespace, DefaultAzureCredential())


class ServiceBusJobQueue:
    def enqueue(self, job_type: str, payload: dict) -> str:
        if job_type not in {"ingest", "query"}:
            raise ValueError("Unknown job type")
        # Deduplicate the same content within one source epoch, without sharing jobs across scopes.
        identity = json.dumps(
            [job_type, payload.get("use_case"), payload.get("source_id"), payload.get("checksum"), payload.get("source_epoch")],
            sort_keys=True,
        ) if job_type == "ingest" else uuid.uuid4().hex
        job_id = hashlib.sha256(identity.encode()).hexdigest()
        settings = get_settings()
        with blob_state() as state, state.lock("outbox"):
            pending = state.read("outbox", {})
            if job_id not in pending and len(pending) >= settings.max_queued_jobs:
                raise OverflowError("Job queue is full")
            previous = state.read("jobs/" + job_id)
            if previous and previous.get("expired"):
                previous = None
            if previous and previous["status"] == "running":
                return job_id
            if previous and previous["status"] == "succeeded":
                from core.adapters.vector_stores.azure_search_adapter import source_key
                current = state.read("manifest", {}).get(
                    source_key(payload.get("use_case", ""), payload.get("source_id", "")), {}
                )
                if current.get("checksum") == payload.get("checksum") and not current.get("deleted"):
                    return job_id
            if previous and previous["status"] != "queued":
                job_id = hashlib.sha256((identity + uuid.uuid4().hex).encode()).hexdigest()
                previous = None
            if not previous:
                state.write("jobs/" + job_id, {
                    "job_id": job_id, "job_type": job_type, "payload": payload,
                    "status": "queued", "created_at": datetime.now(UTC).isoformat(),
                    "completed_at": None, "result": None, "error": None,
                })
            pending[job_id] = True
            state.write("outbox", pending)
            # If sending fails, the persisted outbox is recovered by the worker sweep.
            with service_bus_client() as client, client.get_queue_sender(settings.azure_service_bus_queue) as sender:
                sender.send_messages(ServiceBusMessage(job_id, message_id=job_id))
        return job_id

    def get_status(self, job_id: str) -> dict:
        if len(job_id) != 64 or any(c not in "0123456789abcdef" for c in job_id):
            raise NoSuchJobError("Job not found")
        with blob_state() as state:
            row = state.read("jobs/" + job_id)
        if not row or row.get("expired"):
            raise NoSuchJobError("Job not found")
        return {k: v for k, v in row.items() if k not in {"payload", "job_type"}}
