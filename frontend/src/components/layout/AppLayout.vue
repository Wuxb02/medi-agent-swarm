<script setup lang="ts">
import AppSidebar from './AppSidebar.vue'
import AppHeader from './AppHeader.vue'
</script>

<template>
  <div class="flex h-full min-h-0 overflow-hidden">
    <AppSidebar />
    <div class="flex-1 min-h-0 flex flex-col overflow-hidden">
      <AppHeader />
      <main class="flex-1 min-h-0 overflow-hidden">
        <router-view />
      </main>
    </div>
  </div>
</template>
