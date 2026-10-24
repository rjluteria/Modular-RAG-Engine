"""Private immutable originals; only completed ingestion is eligible for expiry."""

import hashlib
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.adapters.state.blob_state import blob_state
from core.adapters.vector_stores.azure_search_adapter import source_key
from core.config import get_settings


def upload_original(path: Path, *, source_id: str, use_case: str) -> tuple[str, str]:
    from azure.core.exceptions import ResourceExistsError

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    name = source_key(use_case, source_id) + "-" + digest + path.suffix
    source = "sources/" + source_key(use_case, source_id)
    with blob_state() as state, state.lock(source):
        row = state.read(source, {})
        if row.get("deleted") or not row.get("epoch"):
            row = {"epoch": uuid.uuid4().hex, "deleted": False}
            state.write(source, row)
        container = state.service.get_container_client(get_settings().azure_originals_container)
        with path.open("rb") as stream:
            try:
                container.get_blob_client(name).upload_blob(stream, overwrite=False)
            except ResourceExistsError:
                pass
        with state.lock("originals"):
            records = state.read("originals", {})
            records.setdefault(name, {"source_key": source_key(use_case, source_id), "pending": True,
                                      "created_at": datetime.now(UTC).isoformat()})
            state.write("originals", records)
    return name, row["epoch"]


@contextmanager
def materialize_original(name: str):
    if Path(name).name != name or not name:
        raise ValueError("Invalid original reference")
    with tempfile.TemporaryDirectory(prefix="rag-parse-") as directory:
        path = Path(directory) / name
        with blob_state() as state:
            blob = state.service.get_blob_client(get_settings().azure_originals_container, name)
            with path.open("xb") as stream:
                blob.download_blob().readinto(stream)
        yield path
    with blob_state() as state, state.lock("originals"):
        records = state.read("originals", {})
        if name in records:
            records[name]["pending"] = False
            state.write("originals", records)


def delete_originals(*, source_id: str, use_case: str) -> None:
    key = source_key(use_case, source_id)
    with blob_state() as state, state.lock("originals"):
        records = state.read("originals", {})
        container = state.service.get_container_client(get_settings().azure_originals_container)
        for name, row in list(records.items()):
            if row["source_key"] == key:
                from azure.core.exceptions import ResourceNotFoundError
                try:
                    container.delete_blob(name)
                except ResourceNotFoundError:
                    pass
                records.pop(name)
        state.write("originals", records)


def expire_originals(now=None) -> int:
    now = now or datetime.now(UTC)
    deleted = 0
    with blob_state() as state, state.lock("originals"):
        config = state.read("configuration", {})
        days = config.get("originals_retention_days", get_settings().originals_retention_days)
        if days not in {0, 1, 7, 30}:
            raise ValueError("Invalid original retention")
        if not days:
            return 0
        records = state.read("originals", {})
        container = state.service.get_container_client(get_settings().azure_originals_container)
        for name, row in list(records.items()):
            if not row["pending"] and datetime.fromisoformat(row["created_at"]) + timedelta(days=days) <= now:
                from azure.core.exceptions import ResourceNotFoundError
                try:
                    container.delete_blob(name)
                except ResourceNotFoundError:
                    pass
                records.pop(name)
                deleted += 1
        state.write("originals", records)
    return deleted
