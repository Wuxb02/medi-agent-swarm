<script setup lang="ts">
defineProps<{
  suggestions: string[]
}>()

const emit = defineEmits<{
  select: [suggestion: string]
}>()
</script>

<template>
  <div class="flex flex-wrap gap-2">
    <button
      v-for="(s, i) in suggestions"
      :key="i"
      @click="emit('select', s)"
      class="text-xs px-3 py-1.5 bg-blue-50 text-blue-600 rounded-full hover:bg-blue-100 transition border border-blue-200"
    >
      {{ s }}
    </button>
  </div>
</template>
