import json
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from api.auth import authenticate, authorize_use_case, require_admin
from api.limits import enforce_rate_limit
from api.schemas import AsyncJobAcceptedResponse, QueryRequest, QueryResponse
from core.adapters.llms.errors import ProviderConfigurationError, public_model_error
from core.chains.retrieval_chain import build_retrieval_chain
from core.registry import get_job_queue

router = APIRouter(tags=["query"], dependencies=[Depends(authenticate)])
_build_lock = threading.Lock()


def _prepare_chain(request: QueryRequest):
    # Serialize cold model initialization while keeping the event loop responsive.
    with _build_lock:
        return build_retrieval_chain(use_case=request.use_case, top_k=request.top_k, filters=request.filters)


@router.post("/query", response_model=QueryResponse)
async def query_sync(request: QueryRequest, http_request: Request, role: str = Depends(authenticate)):
    await run_in_threadpool(authorize_use_case, role, request.use_case)
    await run_in_threadpool(enforce_rate_limit, http_request)
    started = time.perf_counter()
    try:
        chain = await run_in_threadpool(_prepare_chain, request)
    except ProviderConfigurationError as exc:
        raise HTTPException(503, detail=public_model_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Retrieval unavailable") from exc

    if request.stream:

        async def event_stream():
            try:
                async for event, data in chain.astream_events(request.query):
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
            except Exception as exc:  # noqa: BLE001 -- sanitize upstream streaming errors
                yield f"event: error\ndata: {json.dumps(public_model_error(exc))}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    try:
        result = await chain.ainvoke_with_sources(request.query)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=public_model_error(exc),
        ) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    return {"data": {**result, "latency_ms": latency_ms}}


@router.post(
    "/query/async",
    response_model=AsyncJobAcceptedResponse,
    status_code=202,
    dependencies=[Depends(require_admin)],
)
async def query_async(request: QueryRequest, http_request: Request):
    await run_in_threadpool(enforce_rate_limit, http_request)
    queue = get_job_queue()
    try:
        job_id = await run_in_threadpool(queue.enqueue, "query", request.model_dump())
    except OverflowError as exc:
        raise HTTPException(429, "Job queue is full", headers={"Retry-After": "60"}) from exc
    return {"data": {"job_id": job_id, "status": "queued", "poll_url": f"/api/v1/jobs/{job_id}"}}
