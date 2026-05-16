import api from './client'

export async function getSessions(limit: number = 50, offset: number = 0) {
  const { data } = await api.get('/sessions', { params: { limit, offset } })
  return data.sessions
}

export async function getSessionDetail(sessionId: string) {
  const { data } = await api.get(`/sessions/${sessionId}`)
  return data
}

export async function deleteSession(sessionId: string) {
  await api.delete(`/sessions/${sessionId}`)
}
