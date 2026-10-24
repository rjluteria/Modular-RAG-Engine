"""Typed environment configuration shared by the API, workers and evaluation.

OS environment values override local dotenv files. Service credentials are
supplied by the operator rather than retrieved by application code.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Docker containers get real secrets injected as OS env vars via
    # docker-compose's env_file: .env.local — those always take precedence
    # here regardless of what's below. This env_file tuple exists so
    # host-side scripts (eval.py, one-off debugging, etc.) that run outside
    # Docker still pick up the same .env.local values. Later files in the
    # tuple override earlier ones; missing files are silently skipped.
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"), env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    @model_validator(mode="after")
    def distinct_credentials(self):
        if self.api_admin_token and self.api_admin_token == self.api_user_token:
            raise ValueError("Administrator and user credentials must differ")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if self.hybrid_bm25_weight + self.hybrid_dense_weight <= 0:
            raise ValueError("At least one retrieval weight must be positive")
        return self

    # --- provider selection (the only "environment" switches in the app) ---
    parser_provider: Literal["structured", "unstructured", "doc_intelligence"] = "structured"
    embedder_provider: Literal["sentence_transformers", "azure_openai"] = "sentence_transformers"
    vector_store_provider: Literal["qdrant", "azure_search"] = "qdrant"
    llm_provider: Literal["ollama", "nvidia_nim", "azure_openai"] = "nvidia_nim"
    queue_provider: Literal["rq", "service_bus"] = "rq"

    # --- chunking ---
    chunk_size: int = Field(default=800, ge=1)
    chunk_overlap: int = Field(default=120, ge=0)

    # --- retrieval ---
    hybrid_bm25_weight: float = Field(default=0.4, ge=0)
    hybrid_dense_weight: float = Field(default=0.6, ge=0)
    retrieval_top_k: int = Field(default=8, ge=1, le=50)
    reranker_enabled: bool = False  # only flip once eval shows precision@top-k needs it

    # --- local dev backends ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "rag_chunks"
    redis_url: str = "redis://localhost:6379/0"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    sentence_transformers_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # --- NVIDIA NIM (build.nvidia.com hosted, OpenAI-compatible) ---
    nvidia_api_key: str | None = None
    nvidia_structured_output_method: Literal["json_schema", "json_mode"] = "json_schema"
    nvidia_generation_options: dict = {"reasoning_effort": "low", "reasoning_budget": 1024}
    nvidia_judge_options: dict = {"chat_template_kwargs": {"enable_thinking": False}}
    nvidia_nim_model: str = "nvidia/nemotron-3-super-120b-a12b"

    # --- Azure service configuration ---
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_embedding_deployment: str | None = None
    azure_openai_chat_deployment: str | None = None
    azure_search_endpoint: str | None = None
    azure_search_api_key: str | None = None
    azure_search_index: str = "rag-chunks"
    azure_doc_intelligence_endpoint: str | None = None
    azure_doc_intelligence_api_key: str | None = None
    azure_service_bus_connection_string: str | None = None
    azure_service_bus_queue: str = "rag-jobs"

    # --- Azure shared state and model role configuration ---
    azure_storage_account_url: str | None = None
    azure_state_container: str = "state"
    azure_originals_container: str = "originals"
    azure_service_bus_namespace: str | None = None
    azure_openai_judge_deployment: str | None = None
    azure_generation_model_identity: str = ""
    azure_judge_model_identity: str = ""
    azure_embedding_model_identity: str = ""
    azure_embedding_dimensions: int = 1536
    shared_state_provider: Literal["redis", "azure_blob"] = "redis"
    originals_provider: Literal["local", "azure_blob"] = "local"
    originals_retention_days: Literal[0, 1, 7, 30] = 0
    max_queued_jobs: int = Field(default=100, ge=1, le=1000)
    max_model_output_tokens: int = Field(default=2048, ge=1, le=8192)
    max_model_input_tokens: int = Field(default=16000, ge=1000, le=64000)
    azure_max_corpus_chunks: int = Field(default=2000, ge=1, le=10000)

    # --- observability ---
    applicationinsights_connection_string: str | None = None
    log_level: str = "INFO"

    # --- api ---
    api_auth_dev_key: str = ""  # retained for compatibility; never grants access
    api_admin_token: str = ""
    api_user_token: str = ""
    api_user_use_cases: list[str] = []
    cors_origins: list[str] = []
    upload_dir: str = "/app/tmp/uploads"
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    nvidia_judge_model: str = "nvidia/nemotron-3-super-120b-a12b"
    model_timeout_seconds: float = Field(default=120, gt=0)
    model_max_retries: int = 3
    provider_min_interval_seconds: float = Field(default=15, ge=0)
    provider_wait_timeout_seconds: float = Field(default=600, gt=0)
    provider_cooldown_seconds: int = 90


@lru_cache
def get_settings() -> Settings:
    return Settings()
