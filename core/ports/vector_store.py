"""Vector store selection lives entirely behind LangChain's own VectorStore
interface — adapters just construct and return one. Application code never
imports Qdrant or Azure Search directly."""

from typing import Protocol

from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore


class VectorStoreBuilder(Protocol):
    def build(self, embedder: Embeddings) -> VectorStore: ...
