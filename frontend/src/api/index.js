import axios from 'axios'

const client = axios.create({ baseURL: import.meta.env.VITE_API_BASE || '', timeout: 30000 })
const AUTH_CACHE_TTL_MS = 15000
let authCache = { token: '', value: null, expiresAt: 0, promise: null }

export function clearAuthCache () {
  authCache = { token: '', value: null, expiresAt: 0, promise: null }
}

function loadCurrentUser (force = false) {
  const token = localStorage.getItem('rose_token') || ''
  if (!token) return Promise.reject(new Error('未登录'))
  const now = Date.now()
  if (!force && authCache.token === token && authCache.value && authCache.expiresAt > now) {
    return Promise.resolve(authCache.value)
  }
  if (!force && authCache.token === token && authCache.promise) return authCache.promise

  const pending = client.get('/api/auth/me')
  authCache = { token, value: null, expiresAt: 0, promise: pending }
  pending.then(value => {
    if (authCache.token === token && authCache.promise === pending) {
      authCache = { token, value, expiresAt: Date.now() + AUTH_CACHE_TTL_MS, promise: null }
    }
  }).catch(() => {
    if (authCache.token === token && authCache.promise === pending) clearAuthCache()
  })
  return pending
}

client.interceptors.request.use(config => {
  const token = localStorage.getItem('rose_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})
client.interceptors.response.use(response => {
  const payload = response.data
  if (payload && payload.code && payload.code >= 400) return Promise.reject(new Error(payload.message || '请求失败'))
  if (payload && Object.prototype.hasOwnProperty.call(payload, 'data')) {
    const data = payload.data
    // SpringBootAI keeps the operation message beside the unwrapped data.
    // Preserve it for views that need to show a success toast.
    if (data && typeof data === 'object' && !Array.isArray(data) && payload.message && !Object.prototype.hasOwnProperty.call(data, 'message')) {
      return { ...data, message: payload.message }
    }
    return data
  }
  return payload
}, error => {
  const status = Number(error.response?.status || 0)
  const requestUrl = String(error.config?.url || '')
  if (status === 401 && !requestUrl.includes('/api/auth/login') && !requestUrl.includes('/api/auth/register')) {
    const hadToken = !!localStorage.getItem('rose_token')
    localStorage.removeItem('rose_token')
    clearAuthCache()
    if (hadToken && typeof window !== 'undefined') window.dispatchEvent(new Event('rose:auth-expired'))
  }
  return Promise.reject(new Error(error.response?.data?.message || error.response?.data?.error?.message || error.message))
})

export const api = {
  publicConfig: () => client.get('/api/config/public'),
  login: body => client.post('/api/auth/login', body),
  register: body => client.post('/api/auth/register', body),
  requestPasswordRecovery: body => client.post('/api/auth/password-recovery/request', body),
  contactPasswordRecovery: body => client.post('/api/auth/password-recovery/contact', body),
  resetPassword: body => client.post('/api/auth/password-recovery/reset', body),
  me: options => loadCurrentUser(!!options?.force),
  rotateKey: () => client.post('/api/auth/rotate-key'),
  updateProfile: body => client.patch('/api/auth/profile', body),
  changePassword: body => client.post('/api/auth/password', body),
  sessions: params => client.get('/api/auth/sessions', { params }),
  revokeSession: id => client.delete(`/api/auth/sessions/${id}`),
  revokeOtherSessions: () => client.post('/api/auth/sessions/revoke-others'),
  logout: () => client.post('/api/auth/logout'),
  dashboard: params => client.get('/api/dashboard/summary', { params }),
  models: params => client.get('/api/models', { params }),
  keys: params => client.get('/api/keys', { params }),
  createKey: body => client.post('/api/keys', body),
  revokeKey: id => client.patch(`/api/keys/${id}/revoke`),
  deleteKey: id => client.delete(`/api/keys/${id}`),
  monitoring: params => client.get('/api/monitoring', { params }),
  plans: params => client.get('/api/billing/plans', { params }),
  subscriptionPlans: params => client.get('/api/billing/subscription-plans', { params }),
  subscriptions: params => client.get('/api/billing/subscriptions', { params }),
  entitlements: params => client.get('/api/billing/entitlements', { params }),
  subscribe: body => client.post('/api/billing/subscriptions', body),
  cancelSubscription: id => client.post(`/api/billing/subscriptions/${id}/cancel`),
  renewSubscription: id => client.post(`/api/billing/subscriptions/${id}/renew`),
  setSubscriptionAutoRenew: (id, body) => client.post(`/api/billing/subscriptions/${id}/auto-renew`, body),
  quotas: params => client.get('/api/billing/quotas', { params }),
  orders: params => client.get('/api/billing/orders', { params }),
  createOrder: body => client.post('/api/billing/orders', body),
  personalWechatStatus: () => client.get('/api/payment/personal-wechat/status'),
  pay: tradeNo => client.post(`/api/billing/orders/${tradeNo}/pay`),
  cancelOrder: tradeNo => client.post(`/api/billing/orders/${tradeNo}/cancel`),
  redeemCode: body => client.post('/api/billing/redeem-code', body),
  createRechargeCodes: body => client.post('/api/admin/recharge-codes', body),
  rechargeCodes: params => client.get('/api/admin/recharge-codes', { params }),
  revokeRechargeCode: id => client.post(`/api/admin/recharge-codes/${id}/revoke`),
  adminSummary: () => client.get('/api/admin/summary'),
  dashboardVisualization: () => client.get('/api/dashboard/visualization'),
  adminApplicationConfig: () => client.get('/api/admin/application-config'),
  saveAdminApplicationConfig: body => client.patch('/api/admin/application-config', body),
  restartBackend: () => client.post('/api/admin/application-config/restart'),
  adminLogs: params => client.get('/api/admin/logs', { params }),
  exportUsage: params => client.get('/api/admin/usage-export', { params }),
  adminSubscriptionPlans: params => client.get('/api/admin/subscription-plans', { params }),
  createSubscriptionPlan: body => client.post('/api/admin/subscription-plans', body),
  updateSubscriptionPlan: (id, body) => client.patch(`/api/admin/subscription-plans/${id}`, body),
  deleteSubscriptionPlan: id => client.delete(`/api/admin/subscription-plans/${id}`),
  permanentlyDeleteSubscriptionPlan: id => client.delete(`/api/admin/subscription-plans/${id}/permanent`),
  upstreamSubscriptions: params => client.get('/api/upstream-subscriptions', { params }),
  upstreamGatewayMetrics: () => client.get('/api/upstream-subscriptions/gateway-metrics'),
  createUpstreamSubscription: body => client.post('/api/upstream-subscriptions', body),
  updateUpstreamSubscription: (id, body) => client.patch(`/api/upstream-subscriptions/${id}`, body),
  deleteUpstreamSubscription: id => client.delete(`/api/upstream-subscriptions/${id}`),
  authorizeUpstreamSubscription: body => client.post('/api/upstream-subscriptions/oauth/authorize', body),
  exchangeUpstreamSubscription: body => client.post('/api/upstream-subscriptions/oauth/exchange', body),
  refreshUpstreamSubscription: id => client.post(`/api/upstream-subscriptions/${id}/refresh`),
  testUpstreamSubscription: id => client.post(`/api/upstream-subscriptions/${id}/test`),
  upstreamSubscriptionQuota: (id, refresh = false) => client.get(`/api/upstream-subscriptions/${id}/quota`, { params: { refresh } }),
  upstreamConfigRequests: params => client.get('/api/upstream-subscriptions/config-requests', { params }),
  createUpstreamConfigRequest: body => client.post('/api/upstream-subscriptions/config-requests', body),
  revealUpstreamConfigRequest: id => client.get(`/api/upstream-subscriptions/config-requests/${id}/secret`),
  updateUpstreamConfigRequest: (id, body) => client.patch(`/api/upstream-subscriptions/config-requests/${id}`, body),
  myGroupOverview: () => client.get('/api/auth/group-overview'),
  users: (keyword, params = {}) => client.get('/api/admin/users', { params: { keyword, ...params } }),
  userGroups: (params = {}) => client.get('/api/admin/user-groups', { params }),
  userGroupDetail: (id, params = {}) => client.get(`/api/admin/user-groups/${encodeURIComponent(id)}`, { params }),
  createUserGroup: body => client.post('/api/admin/user-groups', body),
  updateUserGroup: (id, body) => client.patch(`/api/admin/user-groups/${id}`, body),
  deleteUserGroup: id => client.delete(`/api/admin/user-groups/${id}`),
  modelMappings: () => client.get('/api/admin/model-mappings'),
  createModelMapping: body => client.post('/api/admin/model-mappings', body),
  updateModelMapping: (id, body) => client.patch(`/api/admin/model-mappings/${id}`, body),
  deleteModelMapping: id => client.delete(`/api/admin/model-mappings/${id}`),
  createUser: body => client.post('/api/admin/users', body),
  userDetail: (id, params = {}) => client.get(`/api/admin/users/${id}`, { params }),
  userUsage: (id, params = {}) => client.get(`/api/admin/users/${id}/usage`, { params }),
  userQuotas: (id, params = {}) => client.get(`/api/admin/users/${id}/quotas`, { params }),
  createUserQuota: (id, body) => client.post(`/api/admin/users/${id}/quotas`, body),
  updateUserQuota: (userId, quotaId, body) => client.patch(`/api/admin/users/${userId}/quotas/${quotaId}`, body),
  deleteUserQuota: (userId, quotaId) => client.delete(`/api/admin/users/${userId}/quotas/${quotaId}`),
  userSubscriptions: (id, params = {}) => client.get(`/api/admin/users/${id}/subscriptions`, { params }),
  updateUserSubscription: (userId, subscriptionId, body) => client.patch(`/api/admin/users/${userId}/subscriptions/${subscriptionId}`, body),
  deleteUserSubscription: (userId, subscriptionId) => client.delete(`/api/admin/users/${userId}/subscriptions/${subscriptionId}`),
   updateUser: (id, body) => client.patch(`/api/admin/users/${id}`, body),
   deleteUser: id => client.delete(`/api/admin/users/${id}`)
}

export default client
