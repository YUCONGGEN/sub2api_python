<template>
  <section v-if="hasDetails" class="gateway-activity" aria-live="polite">
    <article>
      <header><div><span class="activity-dot active"></span><strong>正在执行</strong></div><b>{{ activePeople }} 人 · 并发任务 {{ activeTaskCount }}</b></header>
      <div v-if="activeUsers.length" class="activity-list">
        <div v-for="item in activeUsers" :key="`active-${item.request_id}`" class="activity-row">
          <div class="activity-user"><router-link :to="`/admin/users/${item.user_id}`">@{{ item.username }}</router-link><span>请求 #{{ item.request_id }}</span></div>
          <div class="activity-tags"><span>模型 {{ item.model || '未知' }}</span><span>推理强度 {{ reasoningLabel(item.reasoning_effort) }}</span><span>该用户并发 {{ item.user_concurrent_tasks || 1 }}</span></div>
          <small>{{ providerLabel(item.provider) }} · 上游账号 #{{ item.account_id }} · 已执行 {{ elapsed(item.started_at) }}</small>
          <small v-if="item.requested_model">请求 {{ item.requested_model }} / {{ reasoningLabel(item.requested_effort) }} → 实际 {{ item.model }} / {{ reasoningLabel(item.reasoning_effort) }} · {{ item.mapping_id ? `规则 #${item.mapping_id} ${item.mapping_name || ''}` : '未命中映射' }} · {{ item.group_name || '未分组' }}</small>
        </div>
      </div>
      <div v-else class="activity-empty">当前没有用户正在执行</div>
    </article>
    <article>
      <header><div><span class="activity-dot queued"></span><strong>正在排队</strong></div><b>{{ queuedPeople }} 人 · 排队任务 {{ queuedTaskCount }}</b></header>
      <div v-if="queuedUsers.length" class="activity-list">
        <div v-for="item in queuedUsers" :key="`queued-${item.request_id}`" class="activity-row">
          <div class="activity-user"><router-link :to="`/admin/users/${item.user_id}`">@{{ item.username }}</router-link><span>请求 #{{ item.request_id }}</span></div>
          <div class="activity-tags"><span>模型 {{ item.model || '未知' }}</span><span>推理强度 {{ reasoningLabel(item.reasoning_effort) }}</span><span>该用户并发 {{ item.user_concurrent_tasks || 0 }}</span></div>
          <small v-if="item.provider === '用户组并发'">{{ item.group_name || '用户组' }} · 每人并发 {{ item.concurrency_limit || 1 }} · 该用户排队 {{ item.user_queued_tasks || 1 }} · 已排队 {{ elapsed(item.queued_at) }}</small><small v-else>{{ providerLabel(item.provider) }} · 等待账号 #{{ item.account_id }} · 该用户排队 {{ item.user_queued_tasks || 1 }} · 已排队 {{ elapsed(item.queued_at) }}</small>
        </div>
      </div>
      <div v-else class="activity-empty">当前没有用户排队</div>
    </article>
    <article>
      <header><div><span class="activity-dot api"></span><strong>API 直连</strong></div><b>{{ apiPeople }} 人 · 调用 {{ apiTaskCount }}</b></header>
      <div v-if="apiUsers.length" class="activity-list">
        <div v-for="item in apiUsers" :key="`api-${item.request_id}`" class="activity-row">
          <div class="activity-user"><router-link :to="`/admin/users/${item.user_id}`">@{{ item.username }}</router-link><span>请求 #{{ item.request_id }}</span></div>
          <div class="activity-tags"><span>模型 {{ item.model || '未知' }}</span><span>推理强度 {{ reasoningLabel(item.reasoning_effort) }}</span><span>{{ item.endpoint || 'API' }}</span></div>
          <small>{{ item.api_provider || 'API 上游' }} · 非共享池 · 已执行 {{ elapsed(item.started_at) }}</small>
          <small v-if="item.requested_model">请求 {{ item.requested_model }} / {{ reasoningLabel(item.requested_effort) }} → 实际 {{ item.model }} / {{ reasoningLabel(item.reasoning_effort) }} · {{ item.mapping_id ? `规则 #${item.mapping_id} ${item.mapping_name || ''}` : '未命中映射' }}</small>
        </div>
      </div>
      <div v-else class="activity-empty">当前没有用户通过 API 直连调用</div>
    </article>
  </section>
</template>

<script>
export default {
  name: 'GatewayActivity',
  props: { gateway: { type: Object, default: () => ({}) } },
  data: () => ({ clock: Date.now(), timer: null }),
  computed: {
    hasDetails () { return Array.isArray(this.gateway.active_users) && Array.isArray(this.gateway.queued_users) },
    activeUsers () { return this.gateway.active_users || [] },
    queuedUsers () { return this.gateway.queued_users || [] },
    apiUsers () { return this.gateway.api_users || [] },
    activeTaskCount () { return Number(this.gateway.concurrent_tasks ?? this.gateway.active_requests ?? this.activeUsers.length) },
    queuedTaskCount () { return Number(this.gateway.queue_waiting ?? this.queuedUsers.length) },
    apiTaskCount () { return Number(this.gateway.api_active_requests ?? this.apiUsers.length) },
    activePeople () { return new Set(this.activeUsers.map(item => item.user_id)).size },
    queuedPeople () { return new Set(this.queuedUsers.map(item => item.user_id)).size },
    apiPeople () { return new Set(this.apiUsers.map(item => item.user_id)).size }
  },
  created () { this.timer = window.setInterval(() => { this.clock = Date.now() }, 1000) },
  beforeUnmount () { window.clearInterval(this.timer) },
  beforeDestroy () { window.clearInterval(this.timer) },
  methods: {
    providerLabel (provider) { return ({ openai: 'OpenAI', claude: 'Claude', grok: 'Grok', kimi: 'Kimi', zhipu: '智谱 GLM', minimax: 'MiniMax', api: 'API 直连' })[provider] || provider || '未知上游' },
    reasoningLabel (effort) {
      const labels = { none: '无', minimal: '最低', low: '低', medium: '中', high: '高', xhigh: '很高', max: '最高', ultra: '超高' }
      const value = String(effort || '').trim().toLowerCase()
      return value ? (labels[value] || value) : '未指定'
    },
    elapsed (value) {
      const started = new Date(value).getTime()
      if (!value || Number.isNaN(started)) return '—'
      const seconds = Math.max(0, Math.floor((this.clock - started) / 1000))
      if (seconds < 60) return `${seconds} 秒`
      const minutes = Math.floor(seconds / 60)
      return minutes < 60 ? `${minutes} 分 ${seconds % 60} 秒` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`
    }
  }
}
</script>

<style scoped>
.gateway-activity{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.gateway-activity article{min-width:0;border:1px solid var(--line,#d8e4ed);border-radius:14px;background:var(--card,#fff);overflow:hidden}.gateway-activity header{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid var(--line,#d8e4ed)}.gateway-activity header>div{display:flex;align-items:center;gap:8px}.gateway-activity header strong{font-size:13px}.gateway-activity header b{font:11px var(--mono);color:var(--muted,#6f8290)}.activity-dot{width:8px;height:8px;border-radius:50%}.activity-dot.active{background:#4aa377;box-shadow:0 0 0 4px #4aa3771f}.activity-dot.queued{background:#ca923c;box-shadow:0 0 0 4px #ca923c1f}.activity-dot.api{background:#5e78c7;box-shadow:0 0 0 4px #5e78c71f}.activity-list{max-height:310px;overflow:auto}.activity-row{padding:13px 16px;border-bottom:1px solid var(--line,#e3ebf0)}.activity-row:last-child{border-bottom:0}.activity-user{display:flex;align-items:center;justify-content:space-between;gap:10px}.activity-user a{color:inherit;font-weight:700;text-decoration:none}.activity-user a:hover{text-decoration:underline}.activity-user span{font:9px var(--mono);color:var(--muted,#748895)}.activity-tags{display:flex;gap:6px;flex-wrap:wrap;margin:9px 0 7px}.activity-tags span{max-width:100%;padding:4px 7px;border:1px solid var(--line,#d8e4ed);border-radius:6px;background:var(--soft,#f4f7f9);font:10px var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.activity-row small{color:var(--muted,#748895);font-size:11px}.activity-empty{padding:28px 16px;text-align:center;color:var(--muted,#748895);font-size:12px}@media(max-width:760px){.gateway-activity{grid-template-columns:1fr}}
</style>
