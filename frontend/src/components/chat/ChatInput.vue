<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  send: [question: string]
}>()

defineProps<{
  disabled?: boolean
}>()

const input = ref('')

function handleSend() {
  const q = input.value.trim()
  if (!q) return
  emit('send', q)
  input.value = ''
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="border-t border-slate-200 bg-white p-4">
    <div class="flex gap-3 items-end max-w-4xl mx-auto">
      <textarea
        v-model="input"
        @keydown="handleKeydown"
        :disabled="disabled"
        placeholder="输入您的健康问题..."
        rows="1"
        class="flex-1 resize-none rounded-xl border border-slate-300 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-slate-100 disabled:cursor-not-allowed min-h-[44px] max-h-32"
      />
      <button
        @click="handleSend"
        :disabled="disabled || !input.trim()"
        class="shrink-0 w-10 h-10 rounded-xl bg-blue-500 text-white flex items-center justify-center hover:bg-blue-600 disabled:bg-slate-300 disabled:cursor-not-allowed transition"
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
  </div>
</template>
