"""Scope hybrid search and source completion to one published Azure snapshot."""

from typing import Any

from langchain_core.retrievers import BaseRetriever

from core.adapters.retrieval.context_selection import complete_source_context
from core.adapters.state.blob_state import blob_state
from core.adapters.vector_stores.azure_search_adapter import corpus, search_client
from core.config import Settings


class AzurePublishedRetriever(BaseRetriever):
    store: Any
    use_case: str | None = None
    top_k: int = 8
    filters: dict | None = None

    def _get_relevant_documents(self, query: str, *, run_manager=None):
        from azure.search.documents.models import VectorizedQuery

        with blob_state() as state, state.lock("manifest"):
            documents = corpus(state, self.use_case, self.filters)
            if not documents:
                return []
            by_id = {d.metadata["chunk_id"]: d for d in documents}
            # IDs are generated hex digests, never user-controlled filter expressions.
            selection = "search.in(id, '" + ",".join(by_id) + "', ',')"
            vector = self.store.embedder.embed_query(query)
            client = search_client()
            try:
                hits = list(client.search(
                    search_text=query,
                    vector_queries=[VectorizedQuery(vector=vector, k_nearest_neighbors=self.top_k,
                                                    fields="vector")],
                    filter=selection, top=self.top_k,
                ))
            finally:
                client.close()
            selected = [by_id[h["id"]] for h in hits if h["id"] in by_id]
            return complete_source_context(selected, documents, self.top_k)


class AzureSearchRetrievalBuilder:
    def build(self, store, settings: Settings, use_case: str | None, top_k: int, filters: dict | None):
        return AzurePublishedRetriever(store=store, use_case=use_case, top_k=top_k, filters=filters)

    def corpus_from_settings(self, settings: Settings, use_case: str | None = None):
        with blob_state() as state, state.lock("manifest"):
            return corpus(state, use_case)

    def corpus(self, store, use_case: str | None = None):
        return self.corpus_from_settings(Settings(), use_case)
