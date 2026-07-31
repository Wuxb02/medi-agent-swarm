<script setup lang="ts">
import { ref, computed } from 'vue'
import { uploadImage, validateImage } from '../../api/image'

const emit = defineEmits<{
  send: [question: string, images?: string[]]
}>()

defineProps<{
  disabled?: boolean
}>()

interface ImageItem {
  file: File
  preview: string
  uploading: boolean
  url?: string
  error?: string
}

const input = ref('')
const selectedImages = ref<ImageItem[]>([])
const imageInputRef = ref<HTMLInputElement | null>(null)
const dragOver = ref(false)

const hasImages = computed(() => selectedImages.value.length > 0)
const anyImagesReady = computed(() =>
  selectedImages.value.some((img) => img.url && !img.uploading),
)

function triggerImageSelect() {
  imageInputRef.value?.click()
}

function handleImageSelect(e: Event) {
  const el = e.target as HTMLInputElement
  if (el.files) {
    addImages(Array.from(el.files))
    el.value = ''
  }
}

function handlePaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items
  if (!items) return
  const imageFiles: File[] = []
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) imageFiles.push(file)
    }
  }
  if (imageFiles.length > 0) {
    e.preventDefault()
    addImages(imageFiles)
  }
}

function handleDrop(e: DragEvent) {
  dragOver.value = false
  const files = e.dataTransfer?.files
  if (files) {
    const imageFiles = Array.from(files).filter((f) => f.type.startsWith('image/'))
    if (imageFiles.length > 0) addImages(imageFiles)
  }
}

function addImages(files: File[]) {
  for (const file of files) {
    // 限制最多 5 张
    if (selectedImages.value.length >= 5) break
    const error = validateImage(file)
    const preview = URL.createObjectURL(file)
    const item: ImageItem = { file, preview, uploading: false, error: error || undefined }
    selectedImages.value.push(item)
    if (!error) {
      uploadSingle(selectedImages.value[selectedImages.value.length - 1])
    }
  }
}

async function uploadSingle(item: ImageItem) {
  item.uploading = true
  try {
    const result = await uploadImage(item.file)
    item.url = result.url
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '上传失败'
    item.error = msg
  } finally {
    item.uploading = false
  }
}

function removeImage(index: number) {
  URL.revokeObjectURL(selectedImages.value[index].preview)
  selectedImages.value.splice(index, 1)
}

function handleSend() {
  const q = input.value.trim()
  if (!q && !hasImages.value) return
  // 全部上传中都阻塞
  if (hasImages.value && !anyImagesReady.value) return

  // 只发送成功上传的图片，过滤失败和上传中的
  const urls = selectedImages.value.filter((img) => img.url && !img.uploading).map((img) => img.url!)
  emit('send', q || '请帮我分析这张图片', urls.length > 0 ? urls : undefined)

  input.value = ''
  for (const img of selectedImages.value) {
    URL.revokeObjectURL(img.preview)
  }
  selectedImages.value = []
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div
    class="border-t border-slate-200 bg-white p-4 relative"
    @dragover.prevent="dragOver = true"
    @dragleave.prevent="dragOver = false"
    @drop.prevent="handleDrop"
  >
    <!-- 图片预览区 -->
    <div v-if="hasImages" class="flex gap-2 mb-3 flex-wrap max-w-4xl mx-auto">
      <div
        v-for="(img, idx) in selectedImages"
        :key="idx"
        class="relative w-16 h-16 rounded-lg border border-slate-200 overflow-hidden shrink-0 group"
      >
        <img :src="img.preview" class="w-full h-full object-cover" alt="预览" />
        <!-- 上传中遮罩 -->
        <div
          v-if="img.uploading"
          class="absolute inset-0 bg-black/40 flex items-center justify-center"
        >
          <svg
            class="w-5 h-5 text-white animate-spin"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              class="opacity-25"
              cx="12" cy="12" r="10"
              stroke="currentColor" stroke-width="4"
            />
            <path
              class="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
        </div>
        <!-- 错误标记 -->
        <div
          v-if="img.error"
          class="absolute inset-0 bg-red-500/80 flex items-center justify-center text-white text-[10px] p-1 text-center leading-tight"
          :title="img.error"
        >
          失败
        </div>
        <!-- 删除按钮 -->
        <button
          @click="removeImage(idx)"
          class="absolute top-0.5 right-0.5 w-4 h-4 bg-black/50 rounded-full text-white items-center justify-center hover:bg-black/70 hidden group-hover:flex transition"
          title="移除图片"
        >
          <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 输入区域 -->
    <div class="flex gap-3 items-end max-w-4xl mx-auto">
      <!-- 图片选择按钮 -->
      <button
        @click="triggerImageSelect"
        :disabled="disabled"
        class="shrink-0 w-10 h-10 rounded-xl border border-slate-300 text-slate-500 flex items-center justify-center hover:bg-slate-100 hover:text-blue-500 disabled:opacity-50 transition"
        title="添加图片"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
          />
        </svg>
      </button>

      <input
        ref="imageInputRef"
        type="file"
        accept="image/jpeg,image/png,image/gif,image/webp"
        multiple
        class="hidden"
        @change="handleImageSelect"
      />

      <textarea
        v-model="input"
        @keydown="handleKeydown"
        @paste="handlePaste"
        :disabled="disabled"
        placeholder="输入您的健康问题...（支持粘贴图片）"
        rows="1"
        class="flex-1 resize-none rounded-xl border border-slate-300 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-slate-100 disabled:cursor-not-allowed min-h-[44px] max-h-32"
      />

      <button
        @click="handleSend"
        :disabled="disabled || (!input.trim() && !hasImages) || (hasImages && !anyImagesReady)"
        class="shrink-0 w-10 h-10 rounded-xl bg-blue-500 text-white flex items-center justify-center hover:bg-blue-600 disabled:bg-slate-300 disabled:cursor-not-allowed transition"
        title="发送"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
          />
        </svg>
      </button>
    </div>

    <!-- 拖拽覆盖层 -->
    <div
      v-if="dragOver"
      class="absolute inset-0 bg-blue-50/80 border-2 border-dashed border-blue-400 rounded-b-lg flex items-center justify-center z-10 pointer-events-none"
    >
      <span class="text-blue-500 text-sm font-medium">释放以添加图片</span>
    </div>
  </div>
</template>
