"""知识库路由"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form

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

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


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
    """列出文档的全部版本。"""
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
    result = delete_document(doc_id)
    if result.chunks_deleted == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    return result


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    doc_type: str = Form("general"),
    disease: str = Form(""),
    source: str = Form("用户上传"),
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
        )
        background_tasks.add_task(_detect_conflicts, result.version_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/documents/{doc_id:path}", response_model=DocumentUploadResponse)
async def update_doc(
    doc_id: str,
    request: DocumentUpdateRequest,
    background_tasks: BackgroundTasks,
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
        )
        background_tasks.add_task(_detect_conflicts, result.version_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


async def _detect_conflicts(version_id: str) -> None:
    """后台检测新激活版本的疑似冲突。"""
    from mediZJ.core.llm_client import LLMClient
    from mediZJ.knowledge.conflict_detector import MedicalConflictDetector

    await MedicalConflictDetector(llm_client=LLMClient()).detect_version(version_id)
