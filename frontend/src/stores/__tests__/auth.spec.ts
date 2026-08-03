import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../api/auth', () => ({
  getCurrentUser: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
}))

import * as authApi from '../../api/auth'
import { useAuthStore } from '../auth'

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('恢复已有登录态', async () => {
    vi.mocked(authApi.getCurrentUser).mockResolvedValue({
      user_id: 'u1',
      username: 'alice',
      role: 'user',
    })
    const store = useAuthStore()

    await store.restore()

    expect(store.isAuthenticated).toBe(true)
    expect(store.user?.username).toBe('alice')
  })

  it('登录并退出后清理用户', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      user_id: 'admin-id',
      username: 'admin',
      role: 'admin',
    })
    vi.mocked(authApi.logout).mockResolvedValue()
    const store = useAuthStore()

    await store.login('admin')
    expect(store.isAdmin).toBe(true)

    await store.logout()
    expect(store.user).toBeNull()
  })
})
