import api from './client'
import type { DocumentSummary, ChunkDetail } from '../types'

export interface KnowledgeSearchRequest {
  query: string
  top_k?: number
  filter_type?: string
}

export async function searchKnowledge(request: KnowledgeSearchRequest) {
  const { data } = await api.post('/knowledge/search', request)
  return data
}

export async function getKnowledgeTypes() {
  const { data } = await api.get('/knowledge/types')
  return data.types
}

export async function getDocuments(): Promise<{ documents: DocumentSummary[]; total: number }> {
  const { data } = await api.get('/knowledge/documents')
  return data
}

export async function getDocumentChunks(
  docId: string,
): Promise<{ doc_id: string; chunks: ChunkDetail[]; total: number }> {
  const { data } = await api.get(`/knowledge/documents/${encodeURIComponent(docId)}/chunks`)
  return data
}

export async function deleteDocument(
  docId: string,
): Promise<{ doc_id: string; chunks_deleted: number }> {
  const { data } = await api.delete(`/knowledge/documents/${encodeURIComponent(docId)}`)
  return data
}

export async function uploadDocument(
  file: File,
  docType: string = 'general',
  disease: string = '',
  source: string = '用户上传',
): Promise<{ doc_id: string; filename: string; chunks_added: number }> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('doc_type', docType)
  formData.append('disease', disease)
  formData.append('source', source)

  const { data } = await api.post('/knowledge/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function updateDocument(
  docId: string,
  content: string,
  docType?: string,
  disease?: string,
  source?: string,
): Promise<{ doc_id: string; chunks_added: number }> {
  const { data } = await api.put(`/knowledge/documents/${encodeURIComponent(docId)}`, {
    content,
    type: docType,
    disease,
    source,
  })
  return data
}
