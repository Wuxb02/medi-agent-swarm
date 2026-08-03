"""知识库路由"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form

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
        return upload_document(
            filename=file.filename,
            content=content,
            doc_type=doc_type,
            disease=disease,
            source=source,
        )
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
        return update_document(
            doc_id=doc_id,
            content=request.content,
            doc_type=request.type,
            disease=request.disease,
            source=request.source,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
