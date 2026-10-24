"""Resolve providers and construct the contracts consumed by routes and chains."""

from functools import lru_cache
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import VectorStore

from core.adapters.embedders.azure_openai_adapter import AzureOpenAIEmbedderBuilder
from core.adapters.embedders.sentence_transformers_adapter import SentenceTransformersEmbedderBuilder
from core.adapters.llms.azure_openai_adapter import AzureOpenAILLMBuilder
from core.adapters.llms.nvidia_nim_adapter import NvidiaNIMLLMBuilder
from core.adapters.llms.ollama_adapter import OllamaLLMBuilder
from core.adapters.parsers.doc_intelligence_adapter import DocIntelligenceParserAdapter
from core.adapters.parsers.structured_adapter import StructuredParserAdapter
from core.adapters.parsers.unstructured_adapter import UnstructuredParserAdapter
from core.adapters.queues.rq_adapter import RQJobQueue
from core.adapters.queues.service_bus_adapter import ServiceBusJobQueue
from core.adapters.retrieval.azure_search_adapter import AzureSearchRetrievalBuilder
from core.adapters.retrieval.qdrant_adapter import QdrantRetrievalBuilder
from core.adapters.vector_stores.azure_search_adapter import AzureSearchVectorStoreBuilder
from core.adapters.vector_stores.qdrant_adapter import QdrantVectorStoreBuilder
from core.config import get_settings
from core.ports.parser import DocumentParser
from core.ports.queue import JobQueue

_EMBEDDER_BUILDERS: dict[str, Any] = {
    "sentence_transformers": SentenceTransformersEmbedderBuilder,
    "azure_openai": AzureOpenAIEmbedderBuilder,
}
_VECTOR_STORE_BUILDERS: dict[str, Any] = {
    "qdrant": QdrantVectorStoreBuilder,
    "azure_search": AzureSearchVectorStoreBuilder,
}
_LLM_BUILDERS: dict[str, Any] = {
    "ollama": OllamaLLMBuilder,
    "nvidia_nim": NvidiaNIMLLMBuilder,
    "azure_openai": AzureOpenAILLMBuilder,
}
_PARSERS: dict[str, type[DocumentParser]] = {
    "structured": StructuredParserAdapter,
    "unstructured": UnstructuredParserAdapter,
    "doc_intelligence": DocIntelligenceParserAdapter,
}
_QUEUES: dict[str, Any] = {
    "rq": RQJobQueue,
    "service_bus": ServiceBusJobQueue,
}


@lru_cache
def get_embedder() -> Embeddings:
    settings = get_settings()
    return _EMBEDDER_BUILDERS[settings.embedder_provider]().build()


@lru_cache
def get_vector_store() -> VectorStore:
    settings = get_settings()
    return _VECTOR_STORE_BUILDERS[settings.vector_store_provider]().build(get_embedder())


def get_llm() -> BaseChatModel:
    """Create chat clients for the caller rather than reuse an earlier worker event loop."""
    settings = get_settings()
    return _LLM_BUILDERS[settings.llm_provider]().build()


@lru_cache
def get_parser() -> DocumentParser:
    settings = get_settings()
    return _PARSERS[settings.parser_provider]()


@lru_cache
def get_job_queue() -> JobQueue:
    settings = get_settings()
    return _QUEUES[settings.queue_provider]()


_RETRIEVAL_BUILDERS: dict[str, Any] = {
    "qdrant": QdrantRetrievalBuilder,
    "azure_search": AzureSearchRetrievalBuilder,
}


def get_retriever(
    use_case: str | None = None, top_k: int | None = None, filters: dict | None = None
) -> BaseRetriever:
    settings = get_settings()
    builder = _RETRIEVAL_BUILDERS[settings.vector_store_provider]()
    return builder.build(get_vector_store(), settings, use_case, top_k or settings.retrieval_top_k, filters)


def get_retrieval_corpus(use_case: str | None = None) -> list[Document]:
    settings = get_settings()
    builder = _RETRIEVAL_BUILDERS[settings.vector_store_provider]()
    metadata_corpus = getattr(builder, "corpus_from_settings", None)
    return (
        metadata_corpus(settings, use_case)
        if metadata_corpus
        else builder.corpus(get_vector_store(), use_case)
    )


def check_vector_store_ready() -> None:
    builder = _VECTOR_STORE_BUILDERS[get_settings().vector_store_provider]()
    probe = getattr(builder, "check_ready", None)
    if probe is None:
        raise NotImplementedError("Selected storage adapter does not support readiness checks")
    probe()


def get_judge_llm() -> BaseChatModel:
    """A fresh judge client avoids sharing generation event-loop connections."""
    settings = get_settings()
    if settings.llm_provider == "azure_openai":
        return AzureOpenAILLMBuilder().build(judge=True)
    if settings.llm_provider != "nvidia_nim" or settings.embedder_provider != "sentence_transformers":
        raise NotImplementedError(
            "Live evaluation currently supports the default model and embedding adapters"
        )
    return NvidiaNIMLLMBuilder().build(judge=True)


def get_answer_llm(schema=None):
    from core.chains.grounding import GroundedAnswer

    schema = GroundedAnswer if schema is None else schema
    builder = _LLM_BUILDERS[get_settings().llm_provider]()
    model = get_llm()
    bind = getattr(builder, "bind_grounded", None)
    return bind(model, schema) if bind else model.with_structured_output(schema)
