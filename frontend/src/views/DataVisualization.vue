<template>
  <section class="page data-visualization">
    <div class="page-head">
      <div><span class="eyebrow">DATA VISUALIZATION</span><h1>数据可视化</h1><p>全站匿名汇总趋势，不包含用户排行、计费来源或订单信息。</p></div>
      <button class="secondary-btn" :disabled="loading" @click="load">{{ loading ? '加载中…' : '刷新' }}</button>
    </div>
    <div v-if="error" class="panel visual-feedback" role="alert"><p>{{ error }}</p><button class="secondary-btn" @click="load">重新加载</button></div>
    <div v-else-if="loading" class="panel visual-feedback" role="status">正在加载汇总数据…</div>
    <div v-else class="visual-grid">
      <article class="panel visual-card">
        <header><div><span class="eyebrow">LAST 7 DAYS</span><h2>每日请求趋势</h2></div><div class="legend"><span><i class="requests"></i>请求</span><span><i class="tokens"></i>Token</span></div></header>
        <div v-if="weeklyDaily.length" class="bar-chart">
          <div v-for="item in weeklyDaily" :key="item.day" class="bar-column">
            <div class="bar-pair"><span><b>{{ compact(item.requests) }}</b><i class="bar requests" :style="{ height: height(item.requests, requestMax) }"></i></span><span><b>{{ compact(item.total_tokens) }}</b><i class="bar tokens" :style="{ height: height(item.total_tokens, tokenMax) }"></i></span></div>
            <small>{{ shortDate(item.day) }}</small>
          </div>
        </div><div v-else class="empty compact-empty">暂无趋势数据</div>
      </article>

      <article class="panel visual-card">
        <header><div><span class="eyebrow">LAST 7 DAYS</span><h2>每日 Token 与费用</h2></div><div class="legend"><span><i class="tokens"></i>Token</span><span><i class="cost"></i>费用</span></div></header>
        <div v-if="dailyPoints.length" class="line-wrap">
          <svg viewBox="0 0 720 250" preserveAspectRatio="none" role="img" aria-label="每日 Token 和费用趋势">
            <g class="grid-lines"><line v-for="y in gridYs" :key="y" x1="22" :y1="y" x2="700" :y2="y" /></g>
            <polyline class="series tokens" :points="linePoints('tokens')"/><polyline class="series cost" :points="linePoints('cost')"/>
            <g v-for="point in dailyPoints" :key="point.day"><circle class="point tokens" :cx="point.x" :cy="point.tokensY" r="3"><title>{{ point.day }} · {{ number(point.total_tokens) }} Token</title></circle><circle class="point cost" :cx="point.x" :cy="point.costY" r="3"><title>{{ point.day }} · ¥{{ money(point.total_cost) }}</title></circle><text :x="point.x" y="232">{{ shortDate(point.day) }}</text></g>
          </svg>
          <div class="scale"><span>Token 最高 {{ number(tokenMax) }}</span><span>费用最高 ¥{{ money(costMax) }}</span></div>
        </div><div v-else class="empty compact-empty">暂无 Token 和费用数据</div>
      </article>

      <article class="panel visual-card">
        <header><div><span class="eyebrow">LAST 7 DAYS</span><h2>用户活动量</h2></div><div class="legend"><span><i class="activity"></i>活跃用户</span></div></header>
        <div v-if="activityPoints.length" class="line-wrap">
          <svg viewBox="0 0 720 250" preserveAspectRatio="none" role="img" aria-label="每日活跃用户趋势">
            <g class="grid-lines"><line v-for="y in gridYs" :key="y" x1="22" :y1="y" x2="700" :y2="y" /></g>
            <polyline class="series activity" :points="activityPath"/>
            <g v-for="point in activityPoints" :key="point.day"><circle class="point activity" :cx="point.x" :cy="point.activeY" r="3"><title>{{ point.day }} · {{ point.active_users }} 位活跃用户</title></circle><text :x="point.x" y="232">{{ shortDate(point.day) }}</text></g>
          </svg>
          <div class="scale"><span>活跃用户最高 {{ activityMax }} 人</span><span>按日统计 API 调用</span></div>
        </div><div v-else class="empty compact-empty">暂无用户活动数据</div>
      </article>

      <article class="panel visual-card">
        <header><div><span class="eyebrow">MODEL USAGE</span><h2>模型使用量</h2></div><div class="legend"><span><i class="month"></i>本月</span><span><i class="total"></i>历史总量</span></div></header>
        <div v-if="topModels.length" class="model-chart"><div v-for="item in topModels" :key="item.model" class="model-column"><div class="model-bars"><span><b>{{ compact(item.month_tokens) }}</b><i class="bar month" :style="{ height: height(item.month_tokens, modelMax) }"></i></span><span><b>{{ compact(item.total_tokens) }}</b><i class="bar total" :style="{ height: height(item.total_tokens, modelMax) }"></i></span></div><strong>{{ item.model }}</strong><small>本月 {{ number(item.month_requests) }} 次 · 历史 {{ number(item.total_requests) }} 次</small><small>¥{{ money(item.month_cost) }} / ¥{{ money(item.total_cost) }}</small></div></div>
        <div v-else class="empty compact-empty">暂无模型使用数据</div>
      </article>
    </div>
  </section>
</template>

<script>
import { api } from '../api'

export default {
  name: 'DataVisualization',
  data: () => ({ analytics: { daily: [], activity: [] }, modelUsage: [], loading: true, error: '', gridYs: [18, 64.5, 111, 157.5, 204] }),
  computed: {
    weeklyDaily () { return (this.analytics.daily || []).slice(-7) },
    weeklyActivity () { return (this.analytics.activity || []).slice(-7) },
    requestMax () { return Math.max(1, ...this.weeklyDaily.map(item => Number(item.requests || 0))) },
    tokenMax () { return Math.max(1, ...this.weeklyDaily.map(item => Number(item.total_tokens || 0))) },
    costMax () { return Math.max(0.0001, ...this.weeklyDaily.map(item => Number(item.total_cost || 0))) },
    activityMax () { return Math.max(1, ...this.weeklyActivity.map(item => Number(item.active_users || 0))) },
    dailyPoints () { return this.points(this.weeklyDaily).map(point => ({ ...point, tokensY: this.y(point.total_tokens, this.tokenMax), costY: this.y(point.total_cost, this.costMax) })) },
    activityPoints () { return this.points(this.weeklyActivity).map(point => ({ ...point, activeY: this.y(point.active_users, this.activityMax) })) },
    activityPath () { return this.activityPoints.map(point => `${point.x},${point.activeY}`).join(' ') },
    topModels () { return [...this.modelUsage].sort((a, b) => Number(b.total_tokens || 0) - Number(a.total_tokens || 0)).slice(0, 4) },
    modelMax () { return Math.max(1, ...this.topModels.map(item => Number(item.total_tokens || 0))) }
  },
  created () { this.load() },
  methods: {
    async load () { this.loading = true; this.error = ''; try { const data = await api.dashboardVisualization(); this.analytics = data.analytics || { daily: [], activity: [] }; this.modelUsage = data.model_usage || [] } catch (error) { this.error = error.message || '数据可视化加载失败' } finally { this.loading = false } },
    points (items) { const span = Math.max(1, items.length - 1); return items.map((item, index) => ({ ...item, x: 22 + 678 * index / span })) },
    y (value, max) { return 18 + 186 * (1 - Number(value || 0) / Math.max(Number(max || 1), 0.0001)) },
    linePoints (series) { return this.dailyPoints.map(point => `${point.x},${series === 'cost' ? point.costY : point.tokensY}`).join(' ') },
    height (value, max) { return `${Math.max(3, Math.min(100, Number(value || 0) / Math.max(Number(max || 1), 0.0001) * 100))}%` },
    number (value) { return Number(value || 0).toLocaleString('zh-CN') },
    money (value) { return Number(value || 0).toFixed(4) },
    compact (value) { const amount = Number(value || 0); const absolute = Math.abs(amount); if (absolute >= 1e9) return `${(amount / 1e9).toFixed(1).replace(/\.0$/, '')}B`; if (absolute >= 1e6) return `${(amount / 1e6).toFixed(1).replace(/\.0$/, '')}M`; if (absolute >= 1e3) return `${(amount / 1e3).toFixed(1).replace(/\.0$/, '')}K`; return Number.isInteger(amount) ? String(amount) : amount.toFixed(2) },
    shortDate (value) { const parts = String(value || '').split('-'); return parts.length === 3 ? `${parts[1]}/${parts[2]}` : value }
  }
}
</script>

<style scoped>
.visual-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.visual-card{min-width:0;min-height:285px}.visual-card header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px}.visual-card h2{margin:4px 0 0;font-size:18px}.legend{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:10px;color:var(--muted);font-size:11px}.legend span{display:inline-flex;align-items:center;gap:5px}.legend i{width:8px;height:8px;border-radius:50%}.requests{background:#4e9eab}.tokens{background:#7667b8}.cost{background:#a57aa8}.activity{background:#58a783}.month{background:#4e9eab}.total{background:#7667b8}.bar-chart{display:flex;align-items:end;gap:10px;height:210px;padding:15px 4px 0;border-bottom:1px solid var(--border)}.bar-column{display:grid;grid-template-rows:1fr auto;gap:7px;flex:1;height:100%;min-width:0;text-align:center}.bar-pair,.model-bars{display:flex;align-items:end;justify-content:center;gap:6px;min-height:0}.bar-pair span,.model-bars span{display:flex;flex:1;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;min-width:0}.bar-pair b,.model-bars b{max-width:100%;overflow:hidden;color:var(--muted);font:9px var(--mono)}.bar{display:block;width:62%;max-width:28px;min-height:4px;border-radius:5px 5px 2px 2px}.bar-column small{font:11px var(--mono);color:var(--muted)}.line-wrap svg{display:block;width:100%;height:205px;overflow:visible}.grid-lines line{stroke:var(--border);stroke-dasharray:4 5}.series{fill:none;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}.series.tokens{stroke:#7667b8}.series.cost{stroke:#a57aa8}.series.activity{stroke:#58a783}.point{stroke:var(--surface);stroke-width:2}.point.tokens{fill:#7667b8}.point.cost{fill:#a57aa8}.point.activity{fill:#58a783}.line-wrap text{fill:var(--muted);font:10px var(--mono);text-anchor:middle}.scale{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font:10px var(--mono)}.model-chart{display:flex;align-items:stretch;gap:12px;min-height:210px}.model-column{display:grid;grid-template-rows:155px auto auto auto;flex:1;gap:6px;min-width:0;text-align:center}.model-bars{height:155px;border-bottom:1px solid var(--border)}.model-column strong,.model-column small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.model-column strong{font-size:12px}.model-column small{color:var(--muted);font-size:10px}.visual-feedback{min-height:180px;display:flex;align-items:center;justify-content:center;gap:18px}@media(max-width:900px){.visual-grid{grid-template-columns:1fr}}@media(max-width:520px){.visual-card{padding:16px}.bar-chart{gap:4px}.model-chart{gap:5px}.legend{max-width:130px}}
</style>
