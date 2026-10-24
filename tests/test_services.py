"""Opt-in tests use disposable collections and a separate Redis instance."""

import os
import uuid

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

pytestmark = pytest.mark.skipif(os.getenv("RAG_SERVICE_TESTS") != "1", reason="isolated services required")


class SyntheticEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [[1.0, float(len(text) % 7), 0.5] for text in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


def test_source_replacement_deduplication_and_isolation(monkeypatch):
    from core.adapters.vector_stores.qdrant_adapter import QdrantVectorStoreBuilder
    from core.config import get_settings
    from core.registry import get_retrieval_corpus

    settings = get_settings()
    monkeypatch.setattr(settings, "qdrant_collection", "synthetic_" + uuid.uuid4().hex)
    store = QdrantVectorStoreBuilder().build(SyntheticEmbeddings())
    monkeypatch.setattr("core.registry.get_vector_store", lambda: store)

    def replace(text, use_case="alpha"):
        return store.replace_source(
            [Document(page_content=text, metadata={"source_id": "guide", "use_case": use_case})],
            source_id="guide",
            use_case=use_case,
        )

    try:
        first = replace("The limit is 42.")
        assert replace("The limit is 42.") == first
        assert store.client.count(settings.qdrant_collection, exact=True).count == 1
        replace("The limit is 99.")
        replace("Private beta value.", "beta")
        chunks = get_retrieval_corpus("alpha")
        assert [doc.page_content for doc in chunks] == ["The limit is 99."]
        from core.adapters.retrieval.qdrant_adapter import QdrantRetrievalBuilder

        builder = QdrantRetrievalBuilder()
        assert builder.build(store, settings, "missing-partition", 1, None).invoke("limit") == []
        assert builder.build(store, settings, "alpha", 1, {"source_id": "absent"}).invoke("limit") == []
        isolated = builder.build(store, settings, "alpha", 2, None).invoke("limit")
        assert isolated and all(doc.metadata["use_case"] == "alpha" for doc in isolated)
        store.delete_source(source_id="guide", use_case="alpha")
        assert get_retrieval_corpus("alpha") == []
        assert len(get_retrieval_corpus("beta")) == 1
    finally:
        store.client.delete_collection(settings.qdrant_collection)
        store.client.close()


def test_duplicate_upload_returns_existing_pending_job(tmp_path):
    from core.adapters.queues.rq_adapter import RQJobQueue

    queue = RQJobQueue()
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_text("synthetic")
    second.write_text("synthetic")
    payload = {"source_id": uuid.uuid4().hex, "use_case": "synthetic", "checksum": "identical"}
    job_id = queue.enqueue("ingest", {**payload, "file_path": str(first)})
    try:
        assert queue.enqueue("ingest", {**payload, "file_path": str(second)}) == job_id
        assert first.exists()
        assert not second.exists()
        assert queue.get_status(job_id)["status"] == "queued"
    finally:
        from rq.job import Job

        Job.fetch(job_id, connection=queue._redis).delete()
        queue._redis.close()


def test_shared_request_counter_rejects_excess_requests():
    from fastapi import HTTPException
    from starlette.requests import Request

    from api.limits import enforce_rate_limit

    token = uuid.uuid4().hex.encode()
    request = Request({"type": "http", "headers": [(b"authorization", b"Bearer " + token)]})
    enforce_rate_limit(request, limit=1)
    same_credential = Request({"type": "http", "headers": [(b"authorization", b"bearer " + token)]})
    with pytest.raises(HTTPException) as error:
        enforce_rate_limit(same_credential, limit=1)
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "60"


def test_worker_description_does_not_expose_query_payload():
    from rq.job import Job

    from core.adapters.queues.rq_adapter import RQJobQueue

    queue = RQJobQueue()
    job_id = queue.enqueue("query", {"query": "synthetic confidential phrase", "use_case": "synthetic"})
    try:
        job = Job.fetch(job_id, connection=queue._redis)
        assert "confidential phrase" not in job.description
        assert "use_case" not in job.description
    finally:
        Job.fetch(job_id, connection=queue._redis).delete()
        queue._redis.close()


def test_failed_staging_preserves_complete_old_revision(monkeypatch):
    from core.adapters.retrieval.qdrant_adapter import load_corpus
    from core.adapters.vector_stores.qdrant_adapter import QdrantVectorStoreBuilder
    from core.adapters.vector_stores.revisions import manifest_key, redis_connection
    from core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "qdrant_collection", "synthetic_" + uuid.uuid4().hex)
    store = QdrantVectorStoreBuilder().build(SyntheticEmbeddings())

    def docs(values):
        return [
            Document(page_content=v, metadata={"source_id": "guide", "use_case": "synthetic"}) for v in values
        ]

    original_add = type(store).add_documents
    try:
        store.replace_source(
            docs(["Old qualified value 42", "Old condition applies"]), source_id="guide", use_case="synthetic"
        )

        def fail_after_first(self, documents, **kwargs):
            original_add(self, documents[:1], ids=kwargs["ids"][:1])
            assert sorted(d.page_content for d in load_corpus(store, "synthetic")) == [
                "Old condition applies",
                "Old qualified value 42",
            ]
            raise RuntimeError("Synthetic interrupted storage")

        with monkeypatch.context() as patch:
            patch.setattr(type(store), "add_documents", fail_after_first)
            with pytest.raises(RuntimeError):
                store.replace_source(
                    docs(["New qualified value 99", "New condition applies"]),
                    source_id="guide",
                    use_case="synthetic",
                )
        assert sorted(d.page_content for d in load_corpus(store, "synthetic")) == [
            "Old condition applies",
            "Old qualified value 42",
        ]
        store.replace_source(
            docs(["New qualified value 99", "New condition applies"]), source_id="guide", use_case="synthetic"
        )
        assert sorted(d.page_content for d in load_corpus(store, "synthetic")) == [
            "New condition applies",
            "New qualified value 99",
        ]
        assert store.client.count(store.collection_name, exact=True).count == 2
    finally:
        store.client.delete_collection(store.collection_name)
        with redis_connection() as connection:
            connection.delete(manifest_key(store.collection_name))
        store.client.close()


def test_interruption_after_publish_keeps_new_revision_visible(monkeypatch):
    from core.adapters.retrieval.qdrant_adapter import load_corpus
    from core.adapters.vector_stores.qdrant_adapter import QdrantVectorStoreBuilder
    from core.adapters.vector_stores.revisions import manifest_key, redis_connection
    from core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "qdrant_collection", "synthetic_" + uuid.uuid4().hex)
    store = QdrantVectorStoreBuilder().build(SyntheticEmbeddings())

    def replace(value):
        return store.replace_source(
            [Document(page_content=value, metadata={"source_id": "guide", "use_case": "synthetic"})],
            source_id="guide",
            use_case="synthetic",
        )

    try:
        replace("Old value 42")
        with monkeypatch.context() as patch:
            patch.setattr(
                store.client,
                "delete",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    RuntimeError("Synthetic superseded-revision interruption")
                ),
            )
            with pytest.raises(RuntimeError):
                replace("New value 99")
        assert [d.page_content for d in load_corpus(store, "synthetic")] == ["New value 99"]
        replace("New value 99")
        assert store.client.count(store.collection_name, exact=True).count == 1
    finally:
        store.client.delete_collection(store.collection_name)
        with redis_connection() as connection:
            connection.delete(manifest_key(store.collection_name))
        store.client.close()


@pytest.mark.asyncio
async def test_provider_cooldown_blocks_other_consumers(monkeypatch):
    from redis import Redis

    from core.adapters.llms.errors import ProviderRateLimitError
    from core.adapters.llms.provider_gate import ProviderGate
    from core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "provider_min_interval_seconds", 0)
    connection = Redis.from_url(settings.redis_url)
    connection.delete("rag:provider:cooldown", "rag:provider:slot", "rag:provider:not-before")
    gate = ProviderGate()
    run_id = uuid.uuid4()

    class Limited(Exception):
        status_code = 429

    try:
        await gate.on_chat_model_start({}, [], run_id=run_id)
        await gate.on_llm_error(Limited(), run_id=run_id)
        assert connection.ttl("rag:provider:cooldown") > 0
        with pytest.raises(ProviderRateLimitError):
            await ProviderGate().on_chat_model_start({}, [], run_id=uuid.uuid4())
        assert not connection.exists("rag:provider:slot")
    finally:
        connection.delete("rag:provider:cooldown", "rag:provider:slot", "rag:provider:not-before")
        connection.close()


def test_pending_upload_identity_does_not_cross_collections(tmp_path, monkeypatch):
    from rq.job import Job

    from core.adapters.queues.rq_adapter import RQJobQueue
    from core.config import get_settings

    queue = RQJobQueue()
    settings = get_settings()
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_text("identical content")
    second.write_text("identical content")
    payload = {"source_id": uuid.uuid4().hex, "use_case": "synthetic", "checksum": "same"}
    jobs = []
    try:
        monkeypatch.setattr(settings, "qdrant_collection", "synthetic_collection_a")
        jobs.append(queue.enqueue("ingest", {**payload, "file_path": str(first)}))
        monkeypatch.setattr(settings, "qdrant_collection", "synthetic_collection_b")
        jobs.append(queue.enqueue("ingest", {**payload, "file_path": str(second)}))
        assert jobs[0] != jobs[1]
        assert first.exists() and second.exists()
    finally:
        for job_id in set(jobs):
            Job.fetch(job_id, connection=queue._redis).delete()
        queue._redis.close()


@pytest.mark.asyncio
async def test_provider_consumers_wait_for_active_requests_and_spacing(monkeypatch):
    import asyncio
    import time

    from redis import Redis

    from core.adapters.llms.provider_gate import ProviderGate
    from core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "provider_min_interval_seconds", 0.08)
    monkeypatch.setattr(settings, "provider_wait_timeout_seconds", 2)
    connection = Redis.from_url(settings.redis_url)
    keys = ("rag:provider:slot", "rag:provider:cooldown", "rag:provider:not-before")
    connection.delete(*keys)
    first, second = ProviderGate(), ProviderGate()
    a, b = uuid.uuid4(), uuid.uuid4()
    try:
        await first.on_chat_model_start({}, [], run_id=a)
        pending = asyncio.create_task(second.on_chat_model_start({}, [], run_id=b))
        await asyncio.sleep(0.03)
        assert not pending.done()
        await first.on_llm_end(None, run_id=a)
        reserved_until = float(connection.get("rag:provider:not-before"))
        await asyncio.wait_for(pending, 2)
        assert time.time() >= reserved_until - 0.002
        await second.on_llm_end(None, run_id=b)
        assert not connection.exists(keys[0])
    finally:
        connection.delete(*keys)
        connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("during_spacing", [False, True])
async def test_cancelled_provider_wait_does_not_leak_or_release_another_owner(monkeypatch, during_spacing):
    import asyncio

    from redis import Redis

    from core.adapters.llms.provider_gate import ProviderGate
    from core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "provider_min_interval_seconds", 0.3)
    monkeypatch.setattr(settings, "provider_wait_timeout_seconds", 2)
    connection = Redis.from_url(settings.redis_url)
    keys = ("rag:provider:slot", "rag:provider:cooldown", "rag:provider:not-before")
    connection.delete(*keys)
    first, second = ProviderGate(), ProviderGate()
    a, b = uuid.uuid4(), uuid.uuid4()
    try:
        await first.on_chat_model_start({}, [], run_id=a)
        owner = connection.get(keys[0])
        if during_spacing:
            await first.on_llm_end(None, run_id=a)
        pending = asyncio.create_task(second.on_chat_model_start({}, [], run_id=b))
        await asyncio.sleep(0.03)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not second._leases
        if during_spacing:
            assert not connection.exists(keys[0])
        else:
            assert connection.get(keys[0]) == owner
            await first.on_llm_end(None, run_id=a)
            assert not connection.exists(keys[0])
    finally:
        connection.delete(*keys)
        connection.close()


def test_provider_gate_supports_synchronous_model_callbacks(monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from redis import Redis

    from core.adapters.llms.provider_gate import ProviderGate
    from core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "provider_min_interval_seconds", 0)
    connection = Redis.from_url(settings.redis_url)
    keys = ("rag:provider:slot", "rag:provider:cooldown", "rag:provider:not-before")
    connection.delete(*keys)
    try:
        model = FakeListChatModel(responses=["synthetic"], callbacks=[ProviderGate()])
        assert model.invoke("generic question").content == "synthetic"
        assert not connection.exists(keys[0])
    finally:
        connection.delete(*keys)
        connection.close()


def test_metadata_reads_match_published_corpus_without_loading_embeddings(monkeypatch):
    from core.adapters.retrieval.qdrant_adapter import load_corpus
    from core.adapters.vector_stores.qdrant_adapter import QdrantVectorStoreBuilder
    from core.adapters.vector_stores.revisions import manifest_key, redis_connection
    from core.config import get_settings
    from core.registry import get_retrieval_corpus

    settings = get_settings()
    monkeypatch.setattr(settings, "qdrant_collection", "synthetic_" + uuid.uuid4().hex)
    store = QdrantVectorStoreBuilder().build(SyntheticEmbeddings())
    try:
        store.replace_source(
            [Document(page_content="Old limit: 42 kg", metadata={"source_id": "guide", "use_case": "alpha"})],
            source_id="guide",
            use_case="alpha",
        )
        store.replace_source(
            [
                Document(
                    page_content="Current limit: 73 kg", metadata={"source_id": "guide", "use_case": "alpha"}
                )
            ],
            source_id="guide",
            use_case="alpha",
        )
        store.replace_source(
            [
                Document(
                    page_content="Other partition: 99 kg", metadata={"source_id": "guide", "use_case": "beta"}
                )
            ],
            source_id="guide",
            use_case="beta",
        )
        expected = load_corpus(store, "alpha")

        def forbidden():
            raise AssertionError("Metadata operation loaded an embedding model or vector store")

        monkeypatch.setattr("core.registry.get_embedder", forbidden)
        monkeypatch.setattr("core.registry.get_vector_store", forbidden)
        actual = get_retrieval_corpus("alpha")
        assert actual == expected
        assert [doc.page_content for doc in actual] == ["Current limit: 73 kg"]
        from fastapi.testclient import TestClient

        from api.main import app

        response = TestClient(app).get("/api/v1/health/ready")
        assert response.status_code == 200
    finally:
        store.client.delete_collection(store.collection_name)
        with redis_connection() as connection:
            connection.delete(manifest_key(store.collection_name))
            for key in connection.scan_iter("rag:revision:" + store.collection_name + ":*"):
                connection.delete(key)
        store.client.close()


def test_empty_backend_metadata_checks_do_not_create_collection(monkeypatch):
    from qdrant_client import QdrantClient

    from core.config import get_settings
    from core.registry import check_vector_store_ready, get_retrieval_corpus

    settings = get_settings()
    monkeypatch.setattr(settings, "qdrant_collection", "synthetic_" + uuid.uuid4().hex)

    def forbidden():
        raise AssertionError("Empty metadata check initialized embedding storage")

    monkeypatch.setattr("core.registry.get_vector_store", forbidden)
    monkeypatch.setattr("core.registry.get_embedder", forbidden)
    client = QdrantClient(url=settings.qdrant_url)
    try:
        assert not client.collection_exists(settings.qdrant_collection)
        check_vector_store_ready()
        assert get_retrieval_corpus("alpha") == []
        assert not client.collection_exists(settings.qdrant_collection)
    finally:
        client.close()


def test_hybrid_retrieval_preserves_distinct_source_provenance_for_identical_text(monkeypatch):
    from core.adapters.retrieval.qdrant_adapter import QdrantRetrievalBuilder
    from core.adapters.vector_stores.qdrant_adapter import QdrantVectorStoreBuilder
    from core.adapters.vector_stores.revisions import manifest_key, redis_connection
    from core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "qdrant_collection", "synthetic_" + uuid.uuid4().hex)
    store = QdrantVectorStoreBuilder().build(SyntheticEmbeddings())
    try:
        for source in ("report-a", "report-b"):
            store.replace_source(
                [
                    Document(
                        page_content="The measured value is 42 kg.",
                        metadata={"source_id": source, "use_case": "alpha"},
                    )
                ],
                source_id=source,
                use_case="alpha",
            )
        docs = QdrantRetrievalBuilder().build(store, settings, "alpha", 4, None).invoke("measured value")
        assert {doc.metadata["source_id"] for doc in docs} == {"report-a", "report-b"}
        assert len({doc.metadata["chunk_id"] for doc in docs}) == 2
    finally:
        store.client.delete_collection(store.collection_name)
        with redis_connection() as connection:
            connection.delete(manifest_key(store.collection_name))
        store.client.close()


def test_context_completion_uses_only_current_filtered_partition(monkeypatch):
    from core.adapters.retrieval.qdrant_adapter import QdrantRetrievalBuilder
    from core.adapters.vector_stores.qdrant_adapter import QdrantVectorStoreBuilder
    from core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "qdrant_collection", "synthetic_" + uuid.uuid4().hex)
    store = QdrantVectorStoreBuilder().build(SyntheticEmbeddings())

    def documents(scope):
        return [
            Document(
                page_content=f"{scope} generic passage {i}",
                metadata={"source_id": "guide", "use_case": scope, "kind": "allowed" if i < 2 else "hidden"},
            )
            for i in range(5)
        ]

    try:
        for scope in ("alpha", "beta"):
            store.replace_source(documents(scope), source_id="guide", use_case=scope)
        builder = QdrantRetrievalBuilder()
        result = builder.build(store, settings, "alpha", 8, {"kind": "allowed"}).invoke("generic passage")
        assert len(result) == 2
        assert all(d.metadata["use_case"] == "alpha" and d.metadata["kind"] == "allowed" for d in result)
        assert len(builder.build(store, settings, "alpha", 1, None).invoke("generic passage")) == 1
        replacement = Document(
            page_content="Current revision only",
            metadata={"source_id": "guide", "use_case": "alpha", "kind": "allowed"},
        )
        store.replace_source([replacement], source_id="guide", use_case="alpha")
        current = builder.build(store, settings, "alpha", 8, {"kind": "allowed"}).invoke("generic passage")
        assert [d.page_content for d in current] == ["Current revision only"]
    finally:
        store.client.delete_collection(settings.qdrant_collection)
        store.client.close()
