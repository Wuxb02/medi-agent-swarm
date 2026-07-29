import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  searchKnowledge,
  getDocuments,
  getDocumentChunks,
  deleteDocument,
  uploadDocument,
  updateDocument,
  getKnowledgeTypes,
} from '../api/knowledge'
import type { DocumentSummary, ChunkDetail } from '../types'

export const useKnowledgeStore = defineStore('knowledge', () => {
  const searchResults = ref<unknown[]>([])
  const documents = ref<DocumentSummary[]>([])
  const currentChunks = ref<ChunkDetail[]>([])
  const knowledgeTypes = ref<string[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchSearch(query: string, topK = 10, filterType?: string) {
    loading.value = true
    error.value = null
    try {
      const data = await searchKnowledge({ query, top_k: topK, filter_type: filterType })
      searchResults.value = data.results || data.items || data || []
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
      searchResults.value = []
    } finally {
      loading.value = false
    }
  }

  async function fetchDocuments() {
    loading.value = true
    error.value = null
    try {
      const data = await getDocuments()
      documents.value = data.documents || []
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
    } finally {
      loading.value = false
    }
  }

  async function fetchChunks(docId: string) {
    try {
      const data = await getDocumentChunks(docId)
      currentChunks.value = data.chunks || []
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
    }
  }

  async function removeDocument(docId: string) {
    try {
      await deleteDocument(docId)
      documents.value = documents.value.filter((d) => d.doc_id !== docId)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
    }
  }

  async function addDocument(file: File, docType = 'general', disease = '', source = '用户上传') {
    try {
      await uploadDocument(file, docType, disease, source)
      await fetchDocuments()
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
    }
  }

  async function saveDocument(
    docId: string,
    content: string,
    docType?: string,
    disease?: string,
    source?: string,
  ) {
    try {
      await updateDocument(docId, content, docType, disease, source)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
    }
  }

  async function fetchTypes() {
    try {
      knowledgeTypes.value = await getKnowledgeTypes()
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
    }
  }

  return {
    searchResults,
    documents,
    currentChunks,
    knowledgeTypes,
    loading,
    error,
    fetchSearch,
    fetchDocuments,
    fetchChunks,
    removeDocument,
    addDocument,
    saveDocument,
    fetchTypes,
  }
})
