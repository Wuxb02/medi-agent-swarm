import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  getPersonalInfo,
  updatePersonalInfo,
  confirmPending,
  dismissPending,
  updateMedicalRecords,
} from '../api/personal'
import type {
  PersonalInfoItem,
  PendingItem,
  MedicalRecord,
  PersonalInfoResponse,
} from '../api/personal'

export const usePersonalStore = defineStore('personal', () => {
  const info = ref<Record<string, string>>({})
  const items = ref<PersonalInfoItem[]>([])
  const pendingItems = ref<PendingItem[]>([])
  const medicalRecords = ref<MedicalRecord[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchInfo() {
    loading.value = true
    error.value = null
    try {
      const data: PersonalInfoResponse = await getPersonalInfo()
      info.value = data.info || {}
      items.value = data.items || []
      pendingItems.value = data.pending_items || []
      medicalRecords.value = data.medical_records || []
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
      items.value = []
      pendingItems.value = []
      medicalRecords.value = []
    } finally {
      loading.value = false
    }
  }

  async function saveInfo(newItems: PersonalInfoItem[]) {
    try {
      await updatePersonalInfo(newItems)
      items.value = newItems
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = `保存失败：${message}`
    }
  }

  async function confirmItem(key: string, value: string) {
    try {
      await confirmPending(key, value)
      pendingItems.value = pendingItems.value.filter((p) => p.key !== key)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
    }
  }

  async function dismissItem(key: string, value: string) {
    try {
      await dismissPending(key, value)
      pendingItems.value = pendingItems.value.filter((p) => p.key !== key)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
    }
  }

  function addRecord(record: MedicalRecord) {
    medicalRecords.value.push(record)
  }

  function removeRecord(index: number) {
    medicalRecords.value.splice(index, 1)
  }

  async function saveRecords() {
    try {
      await updateMedicalRecords(medicalRecords.value)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = `保存病史记录失败：${message}`
    }
  }

  return {
    info,
    items,
    pendingItems,
    medicalRecords,
    loading,
    error,
    fetchInfo,
    saveInfo,
    confirmItem,
    dismissItem,
    addRecord,
    removeRecord,
    saveRecords,
  }
})
