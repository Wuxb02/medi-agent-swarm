import api from './client'
import type { ChunkDetail, DocumentSummary, DocumentVersion, KnowledgeConflict } from '../types'

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

export async function getDocumentVersions(docId: string): Promise<DocumentVersion[]> {
  const { data } = await api.get(`/knowledge/documents/${encodeURIComponent(docId)}/versions`)
  return data.items
}

export async function activateDocumentVersion(docId: string, versionId: string) {
  const { data } = await api.post(
    `/knowledge/documents/${encodeURIComponent(docId)}/versions/${encodeURIComponent(versionId)}/activate`,
  )
  return data
}

export async function getKnowledgeConflicts(
  status?: KnowledgeConflict['review_status'],
): Promise<KnowledgeConflict[]> {
  const { data } = await api.get('/governance/conflicts', { params: { status } })
  return data.items
}

export async function reviewKnowledgeConflict(
  conflictId: string,
  action: 'confirmed' | 'dismissed' | 'resolved',
) {
  const { data } = await api.post(`/governance/conflicts/${conflictId}/${action}`)
  return data
}

export async function pruneExpiredData() {
  const { data } = await api.post('/governance/lifecycle/prune')
  return data
}

export async function deleteUserData(userId: string) {
  const { data } = await api.post(
    `/governance/lifecycle/users/${encodeURIComponent(userId)}/delete`,
  )
  return data
}
