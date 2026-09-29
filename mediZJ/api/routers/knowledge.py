"""知识库路由"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from mediZJ.api.models.knowledge import (
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeTypesResponse,
    DocumentListResponse,
    DocumentChunksResponse,
    DocumentUploadResponse,
    DocumentDeleteResponse,
    DocumentUpdateRequest,
)
from mediZJ.api.services.knowledge_service import (
    search_knowledge, get_knowledge_types,
    list_all_documents, get_document_chunks,
    delete_document, upload_document, update_document,
    activate_document_version, list_document_versions,
)
from mediZJ.api.auth import require_admin
from mediZJ.knowledge.candidate_service import KnowledgeCandidateService
from mediZJ.core.llm_client import LLMClient
from mediZJ.memory.dual_extraction import DualMemoryExtractor

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class TrustedSourceRequest(BaseModel):
    source_url: str


@router.post("/documents/{doc_id:path}/trust")
async def trust_document(
    doc_id: str,
    body: TrustedSourceRequest,
    admin: dict = Depends(require_admin),
):
    """管理员核实当前文档版本的来源后标记为可信。"""
    try:
        return KnowledgeCandidateService().trust_source(
            doc_id, body.source_url, admin["user_id"]
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/candidates")
async def list_candidates(_admin: dict = Depends(require_admin)):
    return {"items": KnowledgeCandidateService().list_candidates()}


@router.get("/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: str, _admin: dict = Depends(require_admin)
):
    try:
        return KnowledgeCandidateService().get_candidate(candidate_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/approve")
async def approve_candidate(
    candidate_id: str, admin: dict = Depends(require_admin)
):
    try:
        return KnowledgeCandidateService().approve(
            candidate_id, admin["user_id"]
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/recheck")
async def recheck_candidate(
    candidate_id: str, _admin: dict = Depends(require_admin)
):
    """对新增或更新的可信库内来源重新核对候选。"""
    service = KnowledgeCandidateService()
    try:
        candidate = service.get_candidate(candidate_id)
        hits = service.evidence_hits(candidate["claim"])
        extractor = DualMemoryExtractor(LLMClient(), None, candidates=service)
        evidence = await extractor._judge_evidence(candidate["claim"], hits)
        return service.update_evidence(candidate_id, evidence)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/reject")
async def reject_candidate(
    candidate_id: str, admin: dict = Depends(require_admin)
):
    try:
        return KnowledgeCandidateService().reject(
            candidate_id, admin["user_id"]
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search(request: KnowledgeSearchRequest):
    """搜索知识库"""
    results = search_knowledge(
        query=request.query,
        top_k=request.top_k,
        filter_type=request.filter_type
    )
    return KnowledgeSearchResponse(results=results, total=len(results))


@router.get("/types", response_model=KnowledgeTypesResponse)
async def get_types():
    """获取知识库类型列表"""
    types = get_knowledge_types()
    return KnowledgeTypesResponse(types=types)


@router.get("/documents", response_model=DocumentListResponse)
async def get_documents():
    """获取知识库文档列表"""
    return list_all_documents()


@router.get("/documents/{doc_id:path}/chunks", response_model=DocumentChunksResponse)
async def get_chunks(doc_id: str):
    """获取文档的所有分块"""
    result = get_document_chunks(doc_id)
    if result.total == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    return result


@router.get("/documents/{doc_id:path}/versions")
async def get_versions(
    doc_id: str,
    _admin: dict = Depends(require_admin),
):
    """列出文档的 Active 和唯一上一版。"""
    return {"items": list_document_versions(doc_id)}


@router.post("/documents/{doc_id:path}/versions/{version_id}/activate")
async def activate_version(
    doc_id: str,
    version_id: str,
    _admin: dict = Depends(require_admin),
):
    """原子激活历史文档版本。"""
    try:
        return activate_document_version(doc_id, version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/documents/{doc_id:path}", response_model=DocumentDeleteResponse)
async def remove_document(
    doc_id: str,
    _admin: dict = Depends(require_admin),
):
    """删除文档"""
    try:
        return delete_document(doc_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    doc_type: str = Form("general"),
    disease: str = Form(""),
    source: str = Form("用户上传"),
    effective_at: Optional[str] = Form(None),
    expires_at: Optional[str] = Form(None),
    _admin: dict = Depends(require_admin),
):
    """上传文件到知识库"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "txt"

    if ext != "txt":
        raise HTTPException(status_code=400, detail=f"暂不支持 .{ext} 格式，目前仅支持 .txt 文件")

    try:
        raw = await file.read()
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件必须为 UTF-8 编码")

    if not content.strip():
        raise HTTPException(status_code=400, detail="文件内容为空")

    try:
        result = upload_document(
            filename=file.filename,
            content=content,
            doc_type=doc_type,
            disease=disease,
            source=source,
            effective_at=effective_at,
            expires_at=expires_at,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/documents/{doc_id:path}", response_model=DocumentUploadResponse)
async def update_doc(
    doc_id: str,
    request: DocumentUpdateRequest,
    _admin: dict = Depends(require_admin),
):
    """更新文档内容"""
    try:
        result = update_document(
            doc_id=doc_id,
            content=request.content,
            doc_type=request.type,
            disease=request.disease,
            source=request.source,
            effective_at=request.effective_at,
            expires_at=request.expires_at,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
