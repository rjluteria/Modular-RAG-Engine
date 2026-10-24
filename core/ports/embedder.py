"""Embedder selection behind LangChain's own Embeddings interface."""

from typing import Protocol

from langchain_core.embeddings import Embeddings


class EmbedderBuilder(Protocol):
    def build(self) -> Embeddings: ...
