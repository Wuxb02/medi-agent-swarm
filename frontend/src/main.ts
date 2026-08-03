import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
import './style.css'
import api from './api/client'
import { useAuthStore } from './stores/auth'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const isAuthProbe = String(error?.config?.url || '').endsWith('/auth/me')
    if (error?.response?.status === 401 && !isAuthProbe) {
      useAuthStore(pinia).clear()
      if (router.currentRoute.value.name !== 'Personal') {
        await router.push({
          name: 'Personal',
          query: { redirect: router.currentRoute.value.fullPath },
        })
      }
    }
    return Promise.reject(error)
  },
)

app.use(router)
app.mount('#app')
