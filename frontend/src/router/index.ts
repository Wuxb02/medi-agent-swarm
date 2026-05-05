import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: () => import('../components/layout/AppLayout.vue'),
      children: [
        { path: '', redirect: '/chat' },
        { path: 'chat', name: 'Chat', component: () => import('../views/ChatView.vue') },
        { path: 'chat/:sessionId', name: 'ChatSession', component: () => import('../views/ChatView.vue') },
        { path: 'knowledge', name: 'Knowledge', component: () => import('../views/KnowledgeView.vue') },
        { path: 'sessions', name: 'Sessions', component: () => import('../views/SessionsView.vue') },
        { path: 'dashboard', name: 'Dashboard', component: () => import('../views/DashboardView.vue') },
        { path: 'personal', name: 'Personal', component: () => import('../views/PersonalView.vue') },
      ],
    },
  ],
})

export default router
