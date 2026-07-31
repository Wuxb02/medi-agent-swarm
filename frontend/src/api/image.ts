import api from './client'

export interface ImageUploadResponse {
  url: string
  filename: string
  size: number
  content_type: string
}

const MAX_IMAGE_SIZE_MB = 10
const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']

export function validateImage(file: File): string | null {
  if (!ALLOWED_TYPES.includes(file.type)) {
    return `不支持的图片格式：${file.type || '未知'}，仅支持 JPEG/PNG/GIF/WebP`
  }
  if (file.size > MAX_IMAGE_SIZE_MB * 1024 * 1024) {
    return `图片过大（${(file.size / 1024 / 1024).toFixed(1)}MB），最大 ${MAX_IMAGE_SIZE_MB}MB`
  }
  return null
}

export async function uploadImage(file: File): Promise<ImageUploadResponse> {
  const formData = new FormData()
  formData.append('file', file)

  const { data } = await api.post<ImageUploadResponse>(
    '/chat/upload-image',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}
