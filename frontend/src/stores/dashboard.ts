import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getDashboardStats } from '../api/dashboard'
import type { DashboardStats } from '../types'

export const useDashboardStore = defineStore('dashboard', () => {
  const stats = ref<DashboardStats | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchStats() {
    loading.value = true
    error.value = null
    try {
      stats.value = await getDashboardStats()
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
      console.error('Failed to load dashboard:', e)
    } finally {
      loading.value = false
    }
  }

  return { stats, loading, error, fetchStats }
})
