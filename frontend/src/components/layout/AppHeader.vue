<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()

const titles: Record<string, string> = {
  Chat: '智能问答',
  ChatSession: '智能问答',
  Knowledge: '知识库',
  Dashboard: '仪表盘',
}

const title = route.name ? titles[route.name as string] || 'MediZJ' : 'MediZJ'
</script>

<template>
  <header class="h-14 bg-white border-b border-slate-200 flex items-center justify-between px-6 shrink-0">
    <h1 class="text-lg font-semibold text-slate-800">{{ title }}</h1>
  </header>
</template>
