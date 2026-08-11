import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: () => import('../components/layout/AppLayout.vue'),
      children: [
        { path: '', redirect: '/chat' },
        { path: 'chat', name: 'Chat', component: () => import('../views/ChatView.vue') },
        {
          path: 'chat/:sessionId',
          name: 'ChatSession',
          component: () => import('../views/ChatView.vue'),
        },
        {
          path: 'knowledge',
          name: 'Knowledge',
          component: () => import('../views/KnowledgeView.vue'),
        },
        {
          path: 'dashboard',
          name: 'Dashboard',
          component: () => import('../views/DashboardView.vue'),
        },
        { path: 'traces', name: 'Traces', component: () => import('../views/TraceView.vue') },
        {
          path: 'trace/:traceId',
          name: 'TraceDetail',
          component: () => import('../views/TraceView.vue'),
        },
        {
          path: 'personal',
          name: 'Personal',
          component: () => import('../views/PersonalView.vue'),
        },
        {
          path: 'evolution',
          name: 'Evolution',
          meta: { requiresAdmin: true },
          component: () => import('../views/EvolutionView.vue'),
        },
      ],
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.restore()
  if (!auth.isAuthenticated && to.name !== 'Personal') {
    return {
      name: 'Personal',
      query: { redirect: to.fullPath },
    }
  }
  if (to.meta.requiresAdmin && !auth.isAdmin) return { name: 'Chat' }
  return true
})

export default router
