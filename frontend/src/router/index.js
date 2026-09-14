import Vue from 'vue'
import Router from 'vue-router'
import { api, clearAuthCache } from '../api'

Vue.use(Router)
const router = new Router({ mode: 'history', routes: [
  { path: '/', redirect: '/dashboard' },
  { path: '/login', component: () => import('../views/Login.vue'), meta: { public: true, guestOnly: true } },
  { path: '/register', component: () => import('../views/Register.vue'), meta: { public: true, guestOnly: true } },
  { path: '/dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/models', component: () => import('../views/Models.vue') },
  { path: '/billing', component: () => import('../views/Billing.vue') },
  { path: '/admin', component: () => import('../views/Admin.vue'), meta: { admin: true } },
  { path: '/admin/user-groups/:id', component: () => import('../views/AdminGroup.vue'), props: true, meta: { admin: true, title: '分组详情' } },
  { path: '/admin/upstream-subscriptions', component: () => import('../views/UpstreamSubscriptions.vue'), meta: { admin: true } },
  { path: '/admin/users/:id', component: () => import('../views/AdminUser.vue'), props: true, meta: { admin: true } },
  { path: '/docs', component: () => import('../views/Docs.vue'), meta: { public: true } },
  { path: '/keys', component: () => import('../views/Keys.vue') },
  { path: '/monitoring', component: () => import('../views/MonitoringPanel.vue') },
  { path: '/profile', component: () => import('../views/ProfilePanel.vue') },
  { path: '*', redirect: '/dashboard' }
] })
router.beforeEach(async (to, from, next) => {
  if (!to.meta.public && !localStorage.getItem('rose_token')) return next('/login')
  if (to.meta.guestOnly && localStorage.getItem('rose_token')) return next('/dashboard')
  if (to.matched.some(route => route.meta.admin)) {
    try {
      const data = await api.me()
      if (data?.user?.role !== 'ADMIN') return next('/dashboard')
    } catch (error) {
      localStorage.removeItem('rose_token')
      clearAuthCache()
      return next('/login')
    }
  }
  next()
})
export default router
