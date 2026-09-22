"""知识治理与数据生命周期 API。"""

from fastapi import APIRouter, Depends, HTTPException

from mediZJ.api.auth import require_admin
from mediZJ.knowledge.catalog import KnowledgeCatalog
from mediZJ.memory.lifecycle import DataLifecycleService


router = APIRouter(prefix="/api/governance", tags=["governance"])


@router.post("/lifecycle/users/{user_id}/delete")
async def delete_user_data(
    user_id: str,
    admin: dict = Depends(require_admin),
):
    return await DataLifecycleService().delete_user(user_id, admin["user_id"])


@router.post("/lifecycle/prune")
async def prune_expired(admin: dict = Depends(require_admin)):
    return await DataLifecycleService().prune_expired(admin["user_id"])


@router.get("/lifecycle/jobs/{job_id}")
async def get_lifecycle_job(
    job_id: str,
    _admin: dict = Depends(require_admin),
):
    job = KnowledgeCatalog().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="清理作业不存在")
    return job


@router.post("/lifecycle/jobs/{job_id}/retry")
async def retry_lifecycle_job(
    job_id: str,
    admin: dict = Depends(require_admin),
):
    try:
        return await DataLifecycleService().retry(job_id, admin["user_id"])
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
