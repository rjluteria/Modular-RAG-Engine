from typing import Any, Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=16000)
    use_case: str = Field(min_length=1, max_length=128)
    top_k: int | None = Field(default=None, ge=1, le=50)
    filters: dict[str, str | int | bool] | None = None
    stream: bool = False


class SourceChunk(BaseModel):
    chunk_id: str | None = None
    source_id: str | None = None
    page_number: int | None = None
    section_title: str | None = None
    score: float | None = None
    page_content: str | None = None
    generation_context: str | None = None


class QueryResponseData(BaseModel):
    answer: str
    sources: list[SourceChunk]
    latency_ms: int


class QueryResponse(BaseModel):
    data: QueryResponseData


class AsyncJobAccepted(BaseModel):
    job_id: str
    status: Literal["queued"]
    poll_url: str


class AsyncJobAcceptedResponse(BaseModel):
    data: AsyncJobAccepted


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: str | None = None
    completed_at: str | None = None


class JobStatusResponse(BaseModel):
    data: JobStatus


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
