import api from './client'

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
