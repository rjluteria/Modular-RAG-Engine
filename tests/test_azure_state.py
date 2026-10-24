"""Credential-free Azure contracts: atomic visibility, scoped contexts and durable jobs."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document


class MemoryState:
    def __init__(self):
        self.rows = {}
        self.container = self
        self.service = SimpleNamespace(get_container_client=lambda name: self)
        self.deleted = []

    def read(self, name, default=None):
        return deepcopy(self.rows.get(name, default))

    def write(self, name, value):
        self.rows[name] = deepcopy(value)

    @contextmanager
    def lock(self, name, **kwargs):
        yield SimpleNamespace(is_set=lambda: False)

    def delete_blob(self, name):
        self.deleted.append(name)

    def delete(self, name):
        self.rows.pop(name, None)

    def list_blobs(self, name_starts_with):
        return [SimpleNamespace(name=n) for n in list(self.rows) if n.startswith(name_starts_with)]


@pytest.fixture
def state(monkeypatch):
    from core.adapters.originals import blob_originals
    from core.adapters.queues import service_bus_adapter
    from core.adapters.retrieval import azure_search_adapter as retrieval
    from core.adapters.vector_stores import azure_search_adapter as storage
    from worker import servicebus_worker

    value = MemoryState()

    @contextmanager
    def connect():
        yield value

    for module in [blob_originals, service_bus_adapter, retrieval, storage, servicebus_worker]:
        monkeypatch.setattr(module, "blob_state", connect)
    return value


class Embedder:
    def embed_documents(self, texts):
        return [[0.1, 0.2] for _ in texts]

    def embed_query(self, text):
        return [0.1, 0.2]


class Search:
    def __init__(self, *, succeeds=True):
        self.succeeds = succeeds
        self.deleted = []
        self.uploaded = []
        self.hits = []
        self.options = {}

    def upload_documents(self, rows):
        self.uploaded.extend(rows)
        return [SimpleNamespace(succeeded=self.succeeds) for _ in rows]

    def delete_documents(self, rows):
        self.deleted.extend(rows)
        return [SimpleNamespace(succeeded=True) for _ in rows]

    def search(self, **kwargs):
        self.options = kwargs
        if kwargs.get("search_text") == "*":
            return [{"id": row["id"]} for row in self.uploaded]
        return self.hits

    def close(self):
        pass


def test_incomplete_indexing_preserves_published_revision(state, monkeypatch):
    from core.adapters.vector_stores import azure_search_adapter as storage

    state.write("manifest", {"existing": {"revision": "old", "count": 1}})
    monkeypatch.setattr(storage, "search_client", lambda: Search(succeeds=False))
    store = storage.AzurePublishedStore(Embedder())
    with pytest.raises(RuntimeError, match="indexing"):
        store.replace_source([Document(page_content="new")], source_id="source", use_case="allowed")
    assert state.read("manifest") == {"existing": {"revision": "old", "count": 1}}


def test_replacement_and_deletion_publish_only_complete_source(state, monkeypatch):
    from core.adapters.vector_stores import azure_search_adapter as storage

    client = Search()
    monkeypatch.setattr(storage, "search_client", lambda: client)
    store = storage.AzurePublishedStore(Embedder())
    first = store.replace_source(
        [Document(page_content="old", metadata={"use_case": "allowed"})], source_id="s", use_case="allowed"
    )
    second = store.replace_source(
        [Document(page_content="new", metadata={"use_case": "allowed"})], source_id="s", use_case="allowed"
    )
    assert first != second
    assert [d.page_content for d in storage.corpus(state, "allowed")] == ["new"]
    assert client.deleted == [{"id": first[0]}]
    store.delete_source(source_id="s", use_case="allowed")
    assert storage.corpus(state, "allowed") == []
    store.delete_source(source_id="s", use_case="allowed")
    assert storage.corpus(state, "allowed") == []


def test_hybrid_query_excludes_foreign_and_unpublished_contexts(state, monkeypatch):
    from core.adapters.retrieval import azure_search_adapter as retrieval
    from core.adapters.vector_stores import azure_search_adapter as storage

    client = Search()
    monkeypatch.setattr(storage, "search_client", lambda: client)
    monkeypatch.setattr(retrieval, "search_client", lambda: client)
    store = storage.AzurePublishedStore(Embedder())
    allowed = store.replace_source(
        [Document(page_content="allowed", metadata={"use_case": "a"})], source_id="s", use_case="a"
    )
    foreign = store.replace_source(
        [Document(page_content="foreign", metadata={"use_case": "b"})], source_id="s", use_case="b"
    )
    client.hits = [{"id": foreign[0]}, {"id": allowed[0]}, {"id": "unpublished"}]
    rows = retrieval.AzurePublishedRetriever(store=store, use_case="a").invoke("question")
    assert [d.page_content for d in rows] == ["allowed"]
    assert foreign[0] not in client.options["filter"]
    assert allowed[0] in client.options["filter"]


def test_failed_send_leaves_durable_outbox_for_recovery(state, monkeypatch):
    from core.adapters.queues import service_bus_adapter as queue

    def unavailable():
        raise ConnectionError("Synthetic failure")

    monkeypatch.setattr(queue, "service_bus_client", unavailable)
    with pytest.raises(ConnectionError):
        queue.ServiceBusJobQueue().enqueue("ingest", {"original_ref": "opaque.txt", "checksum": "a"})
    pending = state.read("outbox")
    assert len(pending) == 1
    row = state.read("jobs/" + next(iter(pending)))
    assert row["status"] == "queued" and row["payload"]["original_ref"] == "opaque.txt"


def test_worker_redelivery_does_not_repeat_completed_job(state, monkeypatch):
    from worker import servicebus_worker as worker

    job_id = "a" * 64
    state.write("jobs/" + job_id, {"status": "queued", "job_type": "ingest", "payload": {}})
    state.write("outbox", {job_id: True})
    calls = []
    monkeypatch.setattr(worker, "run_job", lambda *args: calls.append(args) or {"chunks_ingested": 2})
    worker.process_job(job_id)
    worker.process_job(job_id)
    assert len(calls) == 1
    assert state.read("outbox") == {}
    assert state.read("jobs/" + job_id)["payload"] == {}


def test_retention_keeps_pending_originals_and_index_independent(state):
    from core.adapters.originals.blob_originals import expire_originals

    now = datetime.now(UTC)
    old = (now - timedelta(days=2)).isoformat()
    state.write("configuration", {"originals_retention_days": 1})
    state.write(
        "originals",
        {"pending": {"created_at": old, "pending": True}, "complete": {"created_at": old, "pending": False}},
    )
    state.write("manifest", {"index": "unchanged"})
    assert expire_originals(now) == 1
    assert state.deleted == ["complete"]
    assert "pending" in state.read("originals")
    assert state.read("manifest") == {"index": "unchanged"}


def test_unchanged_revision_is_not_deleted_by_deduplication(state, monkeypatch):
    from core.adapters.vector_stores import azure_search_adapter as storage

    client = Search()
    monkeypatch.setattr(storage, "search_client", lambda: client)
    store = storage.AzurePublishedStore(Embedder())
    docs = [Document(page_content="same", metadata={"source_id": "s", "use_case": "a"})]
    first = store.replace_source(docs, source_id="s", use_case="a")
    assert store.replace_source(docs, source_id="s", use_case="a") == first
    assert client.deleted == []


def test_provider_pacing_rejects_concurrent_calls_and_stale_owner(state, monkeypatch):
    from core.adapters.llms import azure_gate
    from core.adapters.llms.errors import ProviderRateLimitError
    from core.config import get_settings

    @contextmanager
    def connect():
        yield state

    now = [1000.0]
    monkeypatch.setattr(azure_gate, "blob_state", connect)
    monkeypatch.setattr("core.adapters.state.blob_state.blob_state", connect)
    monkeypatch.setattr(get_settings(), "shared_state_provider", "azure_blob")
    monkeypatch.setattr(azure_gate.time, "time", lambda: now[0])
    azure_gate.AzureProviderGate.reserve("new-owner")
    before = state.read("provider-slot")
    azure_gate.AzureProviderGate.finish("old-owner")
    assert state.read("provider-slot") == before
    with pytest.raises(ProviderRateLimitError):
        azure_gate.AzureProviderGate.reserve("another")
    azure_gate.AzureProviderGate.finish("new-owner")
    from core.adapters.llms.provider_gate import remaining_cooldown

    assert remaining_cooldown() == get_settings().provider_min_interval_seconds
    with pytest.raises(ProviderRateLimitError):
        azure_gate.AzureProviderGate.reserve("another")
    now[0] += get_settings().provider_min_interval_seconds + 1
    azure_gate.AzureProviderGate.reserve("another")
    assert state.read("provider-slot")["owner"] == "another"


@pytest.mark.asyncio
async def test_azure_provider_rejects_oversized_input_before_reserving(monkeypatch):
    from types import SimpleNamespace

    from core.adapters.llms import azure_gate
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_model_input_tokens", 1000)

    def unexpected(*args, **kwargs):
        raise AssertionError("Oversized input must not reserve provider capacity")

    monkeypatch.setattr(azure_gate.AzureProviderGate, "reserve", unexpected)
    with pytest.raises(ValueError, match="input limit"):
        await azure_gate.AzureProviderGate().on_chat_model_start(
            {}, [[SimpleNamespace(content="word " * 2000)]], run_id="synthetic"
        )


def test_outbox_recovers_job_record_persisted_before_enqueue_crash(state, monkeypatch):
    from core.adapters.queues import service_bus_adapter as queue

    def unavailable():
        raise ConnectionError("Synthetic failure")

    monkeypatch.setattr(queue, "service_bus_client", unavailable)
    payload = {"original_ref": "opaque.txt", "checksum": "a"}
    with pytest.raises(ConnectionError):
        queue.ServiceBusJobQueue().enqueue("ingest", payload)
    job_id = next(iter(state.read("outbox")))
    state.write("outbox", {})
    with pytest.raises(ConnectionError):
        queue.ServiceBusJobQueue().enqueue("ingest", payload)
    assert state.read("outbox") == {job_id: True}


def test_lease_is_supplied_to_state_writes_and_released():
    from core.adapters.state.blob_state import BlobState

    events = []

    class Lease:
        def renew(self):
            events.append("renew")

        def release(self):
            events.append("release")

    lease = Lease()

    class Blob:
        def upload_blob(self, data, **kwargs):
            events.append(kwargs)

        def acquire_lease(self, **kwargs):
            return lease

    blob = Blob()
    container = SimpleNamespace(get_blob_client=lambda name: blob)
    service = SimpleNamespace(get_container_client=lambda name: container, close=lambda: None)
    state = BlobState(service)
    with state.lock("manifest"):
        state.write("manifest", {"published": "revision"})
    assert {"overwrite": True, "lease": lease} in events
    assert events[-1] == "release"


def test_completed_job_expiry_removes_answers_and_payload(state, monkeypatch):
    from worker import expiry

    @contextmanager
    def connect():
        yield state

    monkeypatch.setattr(expiry, "blob_state", connect)
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    state.write(
        "jobs/" + "a" * 64,
        {"completed_at": old, "payload": {"query": "private"}, "result": {"answer": "private answer"}},
    )
    assert expiry.expire_jobs() == 1
    assert state.read("jobs/" + "a" * 64) == {"expired": True}


def test_blob_credential_closes_even_when_transport_close_fails():
    from core.adapters.state.blob_state import BlobState

    events = []

    def fail():
        raise RuntimeError("transport close failed")

    service = SimpleNamespace(get_container_client=lambda name: object(), close=fail)
    state = BlobState(service)
    state._credential = SimpleNamespace(close=lambda: events.append("credential closed"))
    with pytest.raises(RuntimeError, match="transport close failed"):
        state.close()
    assert events == ["credential closed"]
