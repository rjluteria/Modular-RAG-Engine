from fastapi import APIRouter, Depends, HTTPException
from rq.exceptions import NoSuchJobError
from starlette.concurrency import run_in_threadpool

from api.auth import require_admin
from api.schemas import JobStatusResponse
from core.registry import get_job_queue

router = APIRouter(tags=["jobs"], dependencies=[Depends(require_admin)])


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str):
    queue = get_job_queue()
    try:
        status = await run_in_threadpool(queue.get_status, job_id)
    except NoSuchJobError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "job not found"}
        ) from exc
    return {"data": status}
