import api from './client'

export interface PersonalInfoItem {
  key: string
  value: string
}

export interface PersonalInfoResponse {
  info: Record<string, string>
  items: PersonalInfoItem[]
}

export async function getPersonalInfo(): Promise<PersonalInfoResponse> {
  const { data } = await api.get('/personal')
  return data
}

export async function updatePersonalInfo(
  items: PersonalInfoItem[]
): Promise<PersonalInfoResponse> {
  const { data } = await api.put('/personal', { items })
  return data
}
