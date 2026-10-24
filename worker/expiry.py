"""Expire short-lived job results without retaining query history."""

from datetime import UTC, datetime, timedelta

from core.adapters.state.blob_state import blob_state


def expire_jobs() -> int:
    deleted = 0
    cutoff = datetime.now(UTC) - timedelta(days=1)
    with blob_state() as state:
        pending = state.read("outbox", {})
        for blob in state.container.list_blobs(name_starts_with="jobs/"):
            if blob.name.removeprefix("jobs/") in pending:
                continue
            with state.lock(blob.name):
                row = state.read(blob.name, {})
                completed = row.get("completed_at")
                if completed and datetime.fromisoformat(completed) < cutoff:
                    # The lease must be supplied when deleting a leased state record.
                    state.write(blob.name, {"expired": True})
                    deleted += 1
    return deleted
