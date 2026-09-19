<template>
  <section class="page user-group-overview">
    <div class="page-head">
      <div>
        <span class="eyebrow">MY ACCESS GROUP</span>
        <h1>{{ detail ? detail.group.name : '我的分组' }}</h1>
        <p>查看当前生效分组的访问策略和汇总使用情况。</p>
      </div>
      <button class="secondary-btn" :disabled="loading" @click="load">{{ loading ? '加载中…' : '刷新' }}</button>
    </div>

    <div v-if="error" class="panel group-overview-feedback" role="alert">
      <p>{{ error }}</p><button class="secondary-btn" @click="load">重新加载</button>
    </div>
    <div v-else-if="loading" class="panel group-overview-feedback" role="status">正在加载所在分组…</div>
    <template v-else-if="detail">
      <div class="group-overview-metrics">
        <article class="panel"><span class="eyebrow">GROUP MEMBERS</span><h2>{{ number(detail.totals.member_count) }} <small>人</small></h2><p>分组成员总数</p></article>
        <article class="panel"><span class="eyebrow">TOTAL SPEND</span><h2>¥{{ money(detail.totals.total_cost) }}</h2><p>全组累计调用费用</p></article>
        <article class="panel"><span class="eyebrow">TOTAL TOKENS</span><h2>{{ number(detail.totals.total_tokens) }}</h2><p>全组累计 Token</p></article>
        <article class="panel"><span class="eyebrow">TOTAL REQUESTS</span><h2>{{ number(detail.totals.requests) }}</h2><p>全组累计请求</p></article>
      </div>

      <div class="panel group-overview-policy">
        <div class="panel-head">
          <div><span class="eyebrow">GROUP POLICY</span><h2>当前访问策略</h2></div>
          <span class="status success">{{ sourceLabel }}</span>
        </div>
        <div class="policy-facts">
          <span><small>分组权重</small><strong>{{ number(detail.group.weight) }}</strong></span>
          <span><small>每人并发</small><strong>{{ number(detail.group.concurrency_limit) }}</strong></span>
          <span><small>可用模型</small><strong>{{ modelCountLabel }}</strong></span>
        </div>
        <div class="model-list">
          <span v-for="model in detail.group.allowed_models" :key="model" class="status">{{ model === '*' ? '全部模型' : model }}</span>
          <span v-if="!detail.group.allowed_models.length" class="status pending">未开放模型</span>
        </div>
        <p class="group-description">{{ detail.group.description || '暂无分组说明' }}</p>
        <p class="privacy-note">此页仅提供分组汇总信息，不显示成员名单，也不提供用户查看或管理操作。</p>
      </div>
    </template>
  </section>
</template>

<script>
import { api } from '../api'

export default {
  name: 'UserGroup',
  data: () => ({ detail: null, loading: true, error: '' }),
  computed: {
    sourceLabel () {
      if (this.detail?.group_source !== 'SUBSCRIPTION') return '基础分组'
      return this.detail.group_source_plan_name ? `套餐生效 · ${this.detail.group_source_plan_name}` : '套餐生效'
    },
    modelCountLabel () {
      const models = this.detail?.group?.allowed_models || []
      return models.includes('*') ? '全部' : `${models.length} 个`
    }
  },
  created () { this.load() },
  methods: {
    async load () {
      this.loading = true
      this.error = ''
      try { this.detail = await api.myGroupOverview() } catch (error) { this.detail = null; this.error = error.message || '分组概况加载失败' } finally { this.loading = false }
    },
    number (value) { return Number(value || 0).toLocaleString('zh-CN') },
    money (value) { return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 4, maximumFractionDigits: 4 }) }
  }
}
</script>

<style scoped>
.group-overview-metrics { display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px;margin-bottom:20px; }
.group-overview-metrics .panel { min-width:0; }
.group-overview-metrics h2 { font-size:clamp(22px,2.2vw,34px);overflow-wrap:anywhere;font-variant-numeric:tabular-nums; }
.group-overview-metrics h2 small { font-size:14px; }
.group-overview-metrics p,.group-description { color:var(--muted); }
.group-overview-policy { display:grid;gap:20px; }
.policy-facts { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px; }
.policy-facts span { display:grid;gap:7px;padding:16px;border:1px solid var(--border);border-radius:14px;background:var(--soft); }
.policy-facts small { color:var(--muted); }
.policy-facts strong { font-size:20px; }
.model-list { display:flex;flex-wrap:wrap;gap:9px; }
.group-description { margin:0; }
.privacy-note { margin:0;padding:14px 16px;border-left:3px solid var(--accent);background:var(--soft);color:var(--muted); }
.group-overview-feedback { min-height:160px;display:flex;align-items:center;justify-content:center;gap:18px; }
@media (max-width:1000px) { .group-overview-metrics { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:680px) { .policy-facts { grid-template-columns:1fr; } }
</style>
