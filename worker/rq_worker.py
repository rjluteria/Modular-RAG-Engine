"""Local worker process. Run with: rq worker rag-jobs (or `python -m worker.rq_worker`).
Executes the exact same chain/ingestion code the sync API route uses —
no logic duplicated between sync and async paths."""

import asyncio
import logging
from typing import Any

logger = logging.getLogger("modular_rag.worker")


def run_job(job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        if job_type == "query":
            return _run_query_job(payload)
        if job_type == "ingest":
            return _run_ingest_job(payload)
        raise ValueError("Unknown job type")
    except Exception as exc:  # noqa: BLE001 -- redact upstream failures before RQ logs them
        logger.error("Job execution failed (%s)", type(exc).__name__)
        # RQ persists and logs the exception traceback. Do not retain upstream
        # exception messages that can contain source text or provider payloads.
        raise RuntimeError(f"Job execution failed ({type(exc).__name__})") from None


def _run_query_job(payload: dict[str, Any]) -> dict[str, Any]:
    from core.chains.retrieval_chain import build_retrieval_chain

    chain = build_retrieval_chain(
        use_case=payload.get("use_case"), top_k=payload.get("top_k"), filters=payload.get("filters")
    )
    return asyncio.run(chain.ainvoke_with_sources(payload["query"]))


def _run_ingest_job(payload: dict[str, Any]) -> dict[str, Any]:
    from pathlib import Path

    from rq import get_current_job

    from core.chains.ingestion_chain import ingest_file

    if payload.get("original_ref"):
        from core.adapters.originals.blob_originals import materialize_original
        from core.adapters.state.blob_state import blob_state
        from core.adapters.vector_stores.azure_search_adapter import source_key

        key = "sources/" + source_key(payload["use_case"], payload["source_id"])
        with blob_state() as state, state.lock(key):
            source_state = state.read(key, {})
            if source_state.get("deleted") or source_state.get("epoch") != payload.get("source_epoch"):
                raise ValueError("Source ingestion was superseded by deletion")
            with materialize_original(payload["original_ref"]) as path:
                return ingest_file(
                    str(path), source_id=payload["source_id"], use_case=payload["use_case"],
                    checksum=payload.get("checksum"),
                )

    job = get_current_job()

    def progress(stage: str) -> None:
        if job:
            job.meta["progress"] = stage
            job.save_meta()

    try:
        return ingest_file(
            payload["file_path"],
            source_id=payload["source_id"],
            use_case=payload["use_case"],
            checksum=payload.get("checksum"),
            progress=progress,
        )
    finally:
        Path(payload["file_path"]).unlink(missing_ok=True)


if __name__ == "__main__":
    import redis
    from rq import Worker

    from core.config import get_settings

    settings = get_settings()
    connection = redis.from_url(settings.redis_url)
    worker = Worker(["rag-jobs"], connection=connection)
    worker.work()
