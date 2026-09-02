<template>
  <section v-if="hasDetails" class="gateway-activity" aria-live="polite">
    <article>
      <header><div><span class="activity-dot active"></span><strong>正在执行</strong></div><b>{{ activePeople }} 人 · {{ activeUsers.length }} 个请求</b></header>
      <div v-if="activeUsers.length" class="activity-list">
        <div v-for="item in activeUsers" :key="`active-${item.request_id}`" class="activity-row">
          <div class="activity-user"><router-link :to="`/admin/users/${item.user_id}`">@{{ item.username }}</router-link><span>请求 #{{ item.request_id }}</span></div>
          <p>{{ item.model || '未知模型' }}</p>
          <small>{{ providerLabel(item.provider) }} · 上游账号 #{{ item.account_id }} · 已执行 {{ elapsed(item.started_at) }}</small>
        </div>
      </div>
      <div v-else class="activity-empty">当前没有用户正在执行</div>
    </article>
    <article>
      <header><div><span class="activity-dot queued"></span><strong>正在排队</strong></div><b>{{ queuedPeople }} 人 · {{ queuedUsers.length }} 个请求</b></header>
      <div v-if="queuedUsers.length" class="activity-list">
        <div v-for="item in queuedUsers" :key="`queued-${item.request_id}`" class="activity-row">
          <div class="activity-user"><router-link :to="`/admin/users/${item.user_id}`">@{{ item.username }}</router-link><span>请求 #{{ item.request_id }}</span></div>
          <p>{{ item.model || '未知模型' }}</p>
          <small>{{ providerLabel(item.provider) }} · 等待账号 #{{ item.account_id }} · 已排队 {{ elapsed(item.queued_at) }}</small>
        </div>
      </div>
      <div v-else class="activity-empty">当前没有用户排队</div>
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
    activePeople () { return new Set(this.activeUsers.map(item => item.user_id)).size },
    queuedPeople () { return new Set(this.queuedUsers.map(item => item.user_id)).size }
  },
  created () { this.timer = window.setInterval(() => { this.clock = Date.now() }, 1000) },
  beforeUnmount () { window.clearInterval(this.timer) },
  beforeDestroy () { window.clearInterval(this.timer) },
  methods: {
    providerLabel (provider) { return provider === 'openai' ? 'OpenAI' : provider === 'claude' ? 'Claude' : provider || '未知上游' },
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
.gateway-activity{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.gateway-activity article{min-width:0;border:1px solid var(--line,#d8e4ed);border-radius:14px;background:var(--card,#fff);overflow:hidden}.gateway-activity header{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid var(--line,#d8e4ed)}.gateway-activity header>div{display:flex;align-items:center;gap:8px}.gateway-activity header strong{font-size:13px}.gateway-activity header b{font:11px var(--mono);color:var(--muted,#6f8290)}.activity-dot{width:8px;height:8px;border-radius:50%}.activity-dot.active{background:#4aa377;box-shadow:0 0 0 4px #4aa3771f}.activity-dot.queued{background:#ca923c;box-shadow:0 0 0 4px #ca923c1f}.activity-list{max-height:310px;overflow:auto}.activity-row{padding:13px 16px;border-bottom:1px solid var(--line,#e3ebf0)}.activity-row:last-child{border-bottom:0}.activity-user{display:flex;align-items:center;justify-content:space-between;gap:10px}.activity-user a{color:inherit;font-weight:700;text-decoration:none}.activity-user a:hover{text-decoration:underline}.activity-user span{font:9px var(--mono);color:var(--muted,#748895)}.activity-row p{margin:7px 0 5px;font:11px var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.activity-row small{color:var(--muted,#748895);font-size:11px}.activity-empty{padding:28px 16px;text-align:center;color:var(--muted,#748895);font-size:12px}@media(max-width:760px){.gateway-activity{grid-template-columns:1fr}}
</style>
