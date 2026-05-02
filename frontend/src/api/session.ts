import api from './client'

export async function getSessions(limit = 50) {
  const { data } = await api.get('/sessions', { params: { limit } })
  return data.sessions
}

export async function getSessionDetail(sessionId: string) {
  const { data } = await api.get(`/sessions/${sessionId}`)
  return data
}

export async function deleteSession(sessionId: string) {
  await api.delete(`/sessions/${sessionId}`)
}
