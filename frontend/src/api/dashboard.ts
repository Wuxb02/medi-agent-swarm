import api from './client'

export async function getDashboardStats() {
  const { data } = await api.get('/dashboard/stats')
  return data
}

export async function getHealth() {
  const { data } = await api.get('/health')
  return data
}
