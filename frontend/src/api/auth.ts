import api from './client'

export interface AuthUser {
  user_id: string
  username: string
  role: 'user' | 'admin'
}

export async function login(username: string): Promise<AuthUser> {
  const { data } = await api.post<AuthUser>('/auth/login', { username })
  return data
}

export async function logout(): Promise<void> {
  await api.post('/auth/logout')
}

export async function getCurrentUser(): Promise<AuthUser> {
  const { data } = await api.get<AuthUser>('/auth/me')
  return data
}
