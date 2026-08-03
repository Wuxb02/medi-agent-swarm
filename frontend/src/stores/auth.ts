import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import * as authApi from '../api/auth'
import type { AuthUser } from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null)
  const initialized = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const isAuthenticated = computed(() => user.value !== null)
  const isAdmin = computed(() => user.value?.role === 'admin')

  async function restore() {
    if (initialized.value) return
    try {
      user.value = await authApi.getCurrentUser()
    } catch {
      user.value = null
    } finally {
      initialized.value = true
    }
  }

  async function login(username: string) {
    loading.value = true
    error.value = null
    try {
      user.value = await authApi.login(username.trim())
      initialized.value = true
    } catch (err: unknown) {
      const response = (err as { response?: { data?: { detail?: string } } }).response
      error.value = response?.data?.detail || '登录失败'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function logout() {
    try {
      await authApi.logout()
    } finally {
      user.value = null
      initialized.value = true
    }
  }

  function clear() {
    user.value = null
    initialized.value = true
  }

  return {
    user,
    initialized,
    loading,
    error,
    isAuthenticated,
    isAdmin,
    restore,
    login,
    logout,
    clear,
  }
})
