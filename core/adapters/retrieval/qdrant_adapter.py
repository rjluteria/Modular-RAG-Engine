"""Qdrant filters and hybrid retrieval read one published corpus snapshot."""

from types import SimpleNamespace
from typing import Any

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from qdrant_client.http import models

from core.adapters.retrieval.context_selection import complete_source_context
from core.adapters.vector_stores.revisions import read_lock, redis_connection, visible_revisions
from core.config import Settings


def metadata_filter(use_case: str | None, filters: dict | None = None, revisions: list[str] | None = None):
    conditions: list[Any] = []
    if use_case:
        conditions.append(
            models.FieldCondition(key="metadata.use_case", match=models.MatchValue(value=use_case))
        )
    for key, value in (filters or {}).items():
        conditions.append(models.FieldCondition(key=f"metadata.{key}", match=models.MatchValue(value=value)))
    if revisions is not None:
        visible: list[Any] = [models.IsEmptyCondition(is_empty=models.PayloadField(key="metadata._revision"))]
        if revisions:
            visible.append(
                models.FieldCondition(key="metadata._revision", match=models.MatchAny(any=revisions))
            )
        conditions.append(models.Filter(should=visible))
    return models.Filter(must=conditions) if conditions else None


def _scroll_corpus(store, selection) -> list[Document]:
    documents: list[Document] = []
    offset = None
    while True:
        points, offset = store.client.scroll(
            collection_name=store.collection_name,
            scroll_filter=selection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        documents.extend(
            Document(
                page_content=(p.payload or {}).get("page_content", ""),
                metadata=(p.payload or {}).get("metadata", {}),
            )
            for p in points
        )
        if offset is None:
            return documents


def load_corpus(store, use_case: str | None = None, filters: dict | None = None) -> list[Document]:
    with redis_connection() as connection, read_lock(connection, store.collection_name):
        return _scroll_corpus(
            store, metadata_filter(use_case, filters, visible_revisions(connection, store.collection_name))
        )


class PublishedHybridRetriever(BaseRetriever):
    store: Any
    settings: Settings
    use_case: str | None
    top_k: int
    filters: dict | None

    def _get_relevant_documents(self, query: str, *, run_manager) -> list[Document]:
        from langchain_classic.retrievers import EnsembleRetriever
        from langchain_community.retrievers import BM25Retriever

        with redis_connection() as connection, read_lock(connection, self.store.collection_name):
            selection = metadata_filter(
                self.use_case, self.filters, visible_revisions(connection, self.store.collection_name)
            )
            dense = self.store.as_retriever(search_kwargs={"k": self.top_k, "filter": selection})
            documents = _scroll_corpus(self.store, selection)
            if not documents:
                return dense.invoke(query)
            keyword = BM25Retriever.from_documents(documents, k=self.top_k)
            retriever = EnsembleRetriever(
                retrievers=[keyword, dense],
                weights=[self.settings.hybrid_bm25_weight, self.settings.hybrid_dense_weight],
                id_key="chunk_id",
            )
            return complete_source_context(retriever.invoke(query), documents, self.top_k)


class QdrantRetrievalBuilder:
    def corpus_from_settings(self, settings: Settings, use_case: str | None = None) -> list[Document]:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=settings.qdrant_url, timeout=30)
        try:
            if not client.collection_exists(settings.qdrant_collection):
                return []
            store = SimpleNamespace(client=client, collection_name=settings.qdrant_collection)
            return load_corpus(store, use_case)
        finally:
            client.close()

    def build(
        self, store, settings: Settings, use_case: str | None, top_k: int, filters: dict | None
    ) -> BaseRetriever:
        return PublishedHybridRetriever(
            store=store, settings=settings, use_case=use_case, top_k=top_k, filters=filters
        )

    def corpus(self, store, use_case: str | None = None) -> list[Document]:
        return load_corpus(store, use_case)
