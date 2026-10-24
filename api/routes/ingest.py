import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile

from api.auth import require_admin
from api.limits import enforce_rate_limit
from api.schemas import AsyncJobAcceptedResponse
from core.config import get_settings
from core.registry import get_job_queue, get_parser, get_vector_store
from core.uploads import validate_document

router = APIRouter(tags=["ingest"], dependencies=[Depends(require_admin)])


@router.post("/ingest", response_model=AsyncJobAcceptedResponse, status_code=202)
async def ingest(
    request: Request, file: UploadFile, use_case: str = Form(...), source_id: str | None = Form(None)
):
    filename = Path(file.filename or "").name
    if len(filename) > 256 or (source_id is not None and (not source_id.strip() or len(source_id) > 256)):
        raise HTTPException(422, "Source identifiers must contain 1 to 256 characters")
    if not use_case.strip() or len(use_case) > 128:
        raise HTTPException(422, "A use_case of 1 to 128 characters is required")
    if not get_parser().supports(filename, file.content_type):
        raise HTTPException(415, "Unsupported document format")
    settings = get_settings()
    directory = Path(settings.upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (str(uuid.uuid4()) + Path(filename).suffix.lower())
    size = 0
    checksum = hashlib.sha256()
    try:
        with path.open("xb") as target:
            while block := await file.read(65536):
                size += len(block)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, "Document exceeds upload limit")
                checksum.update(block)
                target.write(block)
        if not size:
            raise HTTPException(422, "Empty document")
        try:
            validate_document(path)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        enforce_rate_limit(request, limit=20)
        payload = {
            "file_path": str(path), "checksum": checksum.hexdigest(),
            "source_id": source_id or filename, "use_case": use_case,
        }
        if settings.originals_provider == "azure_blob":
            from starlette.concurrency import run_in_threadpool

            from core.adapters.originals.blob_originals import upload_original

            original_ref, source_epoch = await run_in_threadpool(
                upload_original, path, source_id=source_id or filename, use_case=use_case
            )
            payload.update(original_ref=original_ref, source_epoch=source_epoch)
            payload.pop("file_path")
        from starlette.concurrency import run_in_threadpool
        try:
            job_id = await run_in_threadpool(get_job_queue().enqueue, "ingest", payload)
        except OverflowError as exc:
            raise HTTPException(429, "Job queue is full", headers={"Retry-After": "60"}) from exc
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
        if settings.originals_provider == "azure_blob":
            path.unlink(missing_ok=True)
    return {"data": {"job_id": job_id, "status": "queued", "poll_url": f"/api/v1/jobs/{job_id}"}}


@router.delete("/documents/{source_id}")
def delete_document(source_id: str, use_case: str):
    delete = getattr(get_vector_store(), "delete_source", None)
    if delete is None:
        raise HTTPException(501, "Selected storage adapter does not support deletion")
    if get_settings().originals_provider == "azure_blob":
        from core.adapters.originals.blob_originals import delete_originals
        from core.adapters.state.blob_state import blob_state
        from core.adapters.vector_stores.azure_search_adapter import source_key

        with blob_state() as state, state.lock("sources/" + source_key(use_case, source_id)):
            state.write("sources/" + source_key(use_case, source_id), {"deleted": True})
            delete(source_id=source_id, use_case=use_case)
            delete_originals(source_id=source_id, use_case=use_case)
    else:
        delete(source_id=source_id, use_case=use_case)
    return {"data": {"status": "deleted"}}
