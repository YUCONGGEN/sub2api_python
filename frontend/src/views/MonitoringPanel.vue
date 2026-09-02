<template>
  <section class="page monitoring-page">
    <div class="page-head">
      <div><div class="eyebrow">OBSERVABILITY / MODEL HEALTH</div><h1>模型监控</h1><p>查看全量模型用量与真实检测历史。</p></div>
      <button class="secondary-btn" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新数据 ↻' }}</button>
    </div>
    <div v-if="error" class="data-error" role="alert"><strong>监控数据加载失败</strong><span>{{ error }}</span><button class="secondary-btn" @click="load">重试</button></div>
    <div class="metric-grid">
      <div class="metric-card highlight"><span>可用模型</span><strong>{{ summary.available || 0 }}/{{ summary.total || 0 }}</strong><small>{{ summary.overall_status || '等待检测' }}</small></div>
      <div class="metric-card"><span>24H 请求</span><strong>{{ compact(summary.requests_24h) }}</strong><small>全量模型，非当前分页</small></div>
      <div class="metric-card"><span>24H Token</span><strong>{{ compact(summary.tokens_24h) }}</strong><small>输入 + 输出</small></div>
      <div class="metric-card"><span>运行质量</span><strong>{{ Number(summary.p95_latency_ms || 0) }} ms</strong><small>P95 延迟 · 429：{{ summary.rate_limited_24h || 0 }}</small></div>
    </div>
    <div v-if="loading && !models.length" class="loading-state" aria-live="polite">正在读取模型健康状态…</div>
    <div v-else class="monitor-grid">
      <article v-for="model in models" :key="model.id" class="monitor-card">
        <div class="monitor-head"><div><span class="model-letter">{{ (model.provider || model.id).slice(0, 1).toUpperCase() }}</span><strong>{{ model.id }}</strong></div><span :class="['availability', model.status === '正常' ? '' : 'offline']"><i></i>{{ model.status }}</span></div>
        <div class="health-bars" :aria-label="`${model.id} 最近 ${(model.health_history || []).length} 次检测`"><span v-for="n in 12" :key="n" :class="healthClass(model, n)" :title="healthTitle(model, n)"></span></div>
        <p class="muted">{{ model.health?.detail || '暂无检测信息' }}<span v-if="model.health?.latency_ms"> · {{ model.health.latency_ms }} ms</span></p>
        <div class="monitor-stats"><span><small>24H 请求</small><b>{{ model.requests_24h || 0 }}</b></span><span><small>24H Token</small><b>{{ compact(model.tokens_24h) }}</b></span><span><small>24H 消费</small><b>¥{{ Number(model.cost_24h || 0).toFixed(4) }}</b></span></div>
        <div class="monitor-foot"><span>{{ model.provider }} · {{ model.group }}</span><router-link to="/docs">调用示例 ↗</router-link></div>
      </article>
    </div>
    <div v-if="!loading && !error && !models.length" class="empty"><strong>暂无监控数据</strong><p>还没有配置可用模型。</p></div>
    <div class="pagination" v-if="pagination.pages > 1"><button class="secondary-btn" :disabled="loading || pagination.page <= 1" @click="changePage(pagination.page - 1)">上一页</button><span>第 {{ pagination.page }} / {{ pagination.pages }} 页，共 {{ pagination.total }} 个模型</span><button class="secondary-btn" :disabled="loading || pagination.page >= pagination.pages" @click="changePage(pagination.page + 1)">下一页</button></div>
  </section>
</template>

<script>
import { api } from '../api'

export default {
  data: () => ({ models: [], summary: {}, error: '', loading: false, timer: null, pagination: { page: 1, pages: 1, total: 0 } }),
  created () { this.load(); this.timer = window.setInterval(() => { this.load(false) }, 30000) },
  beforeUnmount () { window.clearInterval(this.timer) },
  beforeDestroy () { window.clearInterval(this.timer) },
  methods: {
    async load (showLoading = true) {
      if (showLoading) this.loading = true
      this.error = ''
      try {
        const d = await api.monitoring({ page: this.pagination.page, page_size: 12 })
        this.models = d.models || []
        this.summary = d.summary || {}
        this.pagination = d.pagination || this.pagination
      } catch (e) { this.error = e.message || '请检查后端服务后重试' } finally { this.loading = false }
    },
    changePage (page) { this.pagination.page = page; this.load() },
    compact (value) { const n = Number(value || 0); return n >= 1000000 ? `${(n / 1000000).toFixed(2)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}K` : n },
    healthEntry (model, n) { const history = model.health_history || []; return history[n - (12 - history.length) - 1] },
    healthClass (model, n) { const entry = this.healthEntry(model, n); return entry ? (entry.ok ? 'ok' : 'failed') : 'idle' },
    healthTitle (model, n) { const entry = this.healthEntry(model, n); return entry ? `${entry.ok ? '正常' : '异常'} · ${new Date(entry.at).toLocaleString('zh-CN')}` : '暂无样本' }
  }
}
</script>
