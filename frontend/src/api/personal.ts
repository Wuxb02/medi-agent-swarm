import api from './client'

export interface PersonalInfoItem {
  key: string
  value: string
}

export interface PendingItem {
  key: string
  value: string
  source_date: string
  confidence: string
  is_record: boolean
  record_date: string
  symptoms: string
  duration: string
  medication: string
  outcome: string
}

export interface MedicalRecord {
  date: string
  description: string
  symptoms: string
  duration: string
  medication: string
  outcome: string
}

export interface PersonalInfoResponse {
  info: Record<string, string>
  items: PersonalInfoItem[]
  pending_items: PendingItem[]
  medical_records: MedicalRecord[]
}

export async function getPersonalInfo(): Promise<PersonalInfoResponse> {
  const { data } = await api.get('/personal')
  return data
}

export async function updatePersonalInfo(items: PersonalInfoItem[]): Promise<PersonalInfoResponse> {
  const { data } = await api.put('/personal', { items })
  return data
}

export async function confirmPending(key: string, value: string) {
  const { data } = await api.post('/personal/pending/confirm', { key, value })
  return data
}

export async function dismissPending(key: string, value: string) {
  const { data } = await api.post('/personal/pending/dismiss', { key, value })
  return data
}

export async function getMedicalRecords(): Promise<{ records: MedicalRecord[] }> {
  const { data } = await api.get('/personal/records')
  return data
}

export async function updateMedicalRecords(records: MedicalRecord[]) {
  const { data } = await api.put('/personal/records', { records })
  return data
}
