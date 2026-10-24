"""The default local parser resolves without optional cloud/legacy packages."""


def test_parser_registry_resolves_local_adapter(monkeypatch):
    from core.adapters.parsers.structured_adapter import StructuredParserAdapter
    from core.config import get_settings
    from core.registry import get_parser

    monkeypatch.setattr(get_settings(), "parser_provider", "structured")
    get_parser.cache_clear()
    try:
        assert isinstance(get_parser(), StructuredParserAdapter)
    finally:
        get_parser.cache_clear()


def test_corpus_fallback_preserves_existing_adapter_interface(monkeypatch):
    from langchain_core.documents import Document

    from core import registry
    from core.config import get_settings

    store = object()
    expected = [Document(page_content="Generic retained-adapter corpus")]

    class Builder:
        def corpus(self, supplied, use_case):
            assert supplied is store and use_case == "synthetic"
            return expected

    monkeypatch.setattr(get_settings(), "vector_store_provider", "azure_search")
    monkeypatch.setitem(registry._RETRIEVAL_BUILDERS, "azure_search", Builder)
    monkeypatch.setattr(registry, "get_vector_store", lambda: store)
    assert registry.get_retrieval_corpus("synthetic") == expected


def test_readiness_closes_client_when_backend_fails(monkeypatch):
    import pytest
    import qdrant_client

    from core.adapters.vector_stores.qdrant_adapter import QdrantVectorStoreBuilder

    clients = []

    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)

        def get_collections(self):
            raise ConnectionError("Synthetic backend unavailable")

        def close(self):
            self.closed = True

    monkeypatch.setattr(qdrant_client, "QdrantClient", Client)
    with pytest.raises(ConnectionError):
        QdrantVectorStoreBuilder().check_ready()
    assert clients[0].closed


def test_chat_clients_are_not_reused_between_worker_event_loops(monkeypatch):
    import asyncio

    from core import registry

    created = []

    class Builder:
        def build(self):
            client = object()
            created.append(client)
            return client

    monkeypatch.setitem(registry._LLM_BUILDERS, "nvidia_nim", Builder)

    async def client():
        return registry.get_llm()

    first = asyncio.run(client())
    second = asyncio.run(client())
    assert first is not second
    assert len(created) == 2
