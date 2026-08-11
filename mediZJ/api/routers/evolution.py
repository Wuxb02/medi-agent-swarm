"""自进化闭环 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from mediZJ.api.auth import get_current_user, require_admin
from mediZJ.api.models.evolution import (
    ExperienceStatusRequest,
    FeedbackRequest,
    ManualEvaluationRequest,
)
from mediZJ.evolution import EvolutionService
from mediZJ.evolution.source_catalog import read_source_snippet
from mediZJ.evolution.storage import RollbackBlockedError

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


@router.post("/feedback")
async def submit_feedback(
    payload: FeedbackRequest,
    user: dict = Depends(get_current_user),
):
    try:
        return EvolutionService().submit_feedback(
            payload.assistant_message_id,
            user["user_id"],
            payload.rating,
            list(payload.reason_codes),
            payload.comment.strip(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/feedback/{message_id}")
async def get_feedback(
    message_id: int,
    user: dict = Depends(get_current_user),
):
    feedback = EvolutionService().storage.get_feedback(
        message_id,
        user["user_id"],
    )
    return {"feedback": feedback}


@router.get("/overview")
async def get_overview(_admin: dict = Depends(require_admin)):
    return EvolutionService().storage.overview()


@router.get("/evaluations")
async def list_evaluations(
    limit: int = Query(100, ge=1, le=500),
    _admin: dict = Depends(require_admin),
):
    return {"items": EvolutionService().storage.list_evaluations(limit)}


@router.get("/failures")
async def list_failures(
    limit: int = Query(100, ge=1, le=500),
    _admin: dict = Depends(require_admin),
):
    return {"items": EvolutionService().storage.list_failures(limit)}


@router.get("/sources/{source_id}")
async def get_source_snippet(
    source_id: str,
    radius: int = Query(18, ge=5, le=50),
    _admin: dict = Depends(require_admin),
):
    """读取失败归因映射的白名单源码片段。"""
    try:
        return read_source_snippet(source_id, radius)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/experiences")
async def list_experiences(
    limit: int = Query(100, ge=1, le=500),
    status: str | None = None,
    _admin: dict = Depends(require_admin),
):
    return {
        "items": EvolutionService().storage.list_experiences(limit, status)
    }


@router.get("/releases")
async def list_releases(
    limit: int = Query(50, ge=1, le=200),
    _admin: dict = Depends(require_admin),
):
    return {"items": EvolutionService().storage.list_releases(limit)}


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(100, ge=1, le=500),
    status: str | None = None,
    _admin: dict = Depends(require_admin),
):
    allowed = {None, "pending", "running", "failed", "superseded", "completed"}
    if status not in allowed:
        raise HTTPException(status_code=422, detail="非法任务状态")
    return {"items": EvolutionService().storage.list_jobs(limit, status)}


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    _admin: dict = Depends(require_admin),
):
    if not EvolutionService().storage.retry_job(job_id):
        raise HTTPException(status_code=404, detail="失败任务不存在")
    return {"queued": True}


@router.post("/evaluations")
async def enqueue_evaluation(
    payload: ManualEvaluationRequest,
    admin: dict = Depends(require_admin),
):
    try:
        job_id = EvolutionService().enqueue_manual(payload.assistant_message_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job_id, "queued": job_id is not None}


@router.post("/experiences/{experience_id}/status")
async def update_experience_status(
    experience_id: str,
    payload: ExperienceStatusRequest,
    admin: dict = Depends(require_admin),
):
    try:
        updated = EvolutionService().storage.apply_experience_action(
            experience_id,
            payload.action,
            admin["user_id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="经验不存在")
    return {"updated": True}


@router.post("/releases/{version}/rollback")
async def rollback_release(
    version: int,
    admin: dict = Depends(require_admin),
):
    try:
        rolled_back = EvolutionService().storage.rollback_release(
            version,
            admin["user_id"],
        )
    except RollbackBlockedError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "blockers": exc.blockers},
        ) from exc
    if not rolled_back:
        raise HTTPException(status_code=404, detail="发布版本不存在")
    return {"rolled_back": True}
