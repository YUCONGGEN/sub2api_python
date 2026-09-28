<template>
  <section class="page models-page">
    <div class="page-head">
      <div>
        <div class="eyebrow">CATALOG / OPENAI COMPATIBLE</div>
        <h1>模型广场</h1>
        <p>可查看 {{ pagination.total || 0 }} 个模型及定价；价格已计入倍率，映射请求按实际调用的目标模型计费。</p>
      </div>
      <router-link class="secondary-btn" to="/monitoring">模型监控 ↗</router-link>
    </div>

    <div class="models-toolbar">
      <div class="search"><span>⌕</span><input v-model="query" placeholder="搜索模型名称、供应商或标签" /></div>
      <div class="view-toggle">
        <button :class="{ active: priceMode === 'standard' }" @click="priceMode = 'standard'">标准</button>
        <button :class="{ active: priceMode === 'input' }" @click="priceMode = 'input'">/ 1M</button>
        <button :class="{ active: priceMode === 'token' }" @click="priceMode = 'token'">/ 1K</button>
      </div>
    </div>

    <div class="model-browser">
      <aside class="filter-panel">
        <div class="panel-head"><div><span class="eyebrow">FILTERS</span><h2>筛选</h2></div><button class="text-btn" @click="reset">重置</button></div>
        <label class="filter-label">分组<select v-model="group"><option value="">所有分组</option><option v-for="item in groups" :key="item" :value="item">{{ item }}</option></select></label>
        <label class="filter-label">供应商<select v-model="provider"><option value="">所有供应商</option><option v-for="item in providers" :key="item" :value="item">{{ item }}</option></select></label>
        <label class="filter-label">状态<select v-model="availability"><option value="">全部状态</option><option value="enabled">可用</option><option value="disabled">不可用/停用</option></select></label>
        <p class="muted">状态由服务端自动检测上游连接、密钥和模型目录。</p>
      </aside>

      <div class="models-results">
        <div class="results-head"><strong>{{ pagination.total || 0 }} 个模型</strong><span>默认：{{ defaultModel }}</span></div>
        <div class="model-grid">
          <article v-for="model in models" :key="model.id" class="model-card">
            <div class="model-top"><span class="model-letter">{{ (model.provider || model.id).slice(0, 1).toUpperCase() }}</span><span :class="['availability', model.status === '正常' ? '' : 'offline']"><i></i>{{ model.status || (model.enabled ? '待检查' : '停用') }}</span></div>
            <h2>{{ model.id }}</h2><p>{{ model.description }}</p>
            <div class="model-tags"><span>{{ model.provider }}</span><span>{{ model.endpoint }}</span><span>{{ model.group }}</span><span v-if="model.reasoning_effort">默认推理 {{ model.reasoning_effort }}</span></div>
            <div class="price-row"><div><small>输入 / {{ priceMode === 'token' ? '1K' : '1M' }} tokens</small><strong>{{ currencySymbol(model) }}{{ displayPrice(model, 'input') }}</strong></div><div><small>输出 / {{ priceMode === 'token' ? '1K' : '1M' }} tokens</small><strong>{{ currencySymbol(model) }}{{ displayPrice(model, 'output') }}</strong></div></div>
            <div v-if="model.health && model.status !== '正常'" class="muted">{{ model.health.detail }}</div>
            <div class="model-actions"><router-link to="/docs" class="model-link">查看接入方式 <span>↗</span></router-link><button class="icon-btn" title="复制模型 ID" @click="copy(model.id)">⧉</button></div>
          </article>
        </div>
        <div v-if="!models.length" class="empty"><div class="empty-icon">⌕</div><strong>没有匹配的模型</strong><p>尝试清空搜索或筛选条件。</p></div>
        <div v-if="pagination.pages > 1" class="pagination"><button class="secondary-btn" :disabled="pagination.page <= 1" @click="changePage(pagination.page - 1)">上一页</button><span>第 {{ pagination.page }} / {{ pagination.pages }} 页，共 {{ pagination.total }} 个模型</span><button class="secondary-btn" :disabled="pagination.page >= pagination.pages" @click="changePage(pagination.page + 1)">下一页</button></div>
      </div>
    </div>
  </section>
</template>

<script>
import { api } from '../api'
import { copyToClipboard, notify } from '../ui'

export default {
  data: () => ({ query: '', group: '', provider: '', availability: '', priceMode: 'standard', models: [], defaultModel: '', facets: { groups: [], providers: [] }, pagination: { page: 1, pages: 1, total: 0 }, searchTimer: null }),
  computed: { groups () { return this.facets.groups || [] }, providers () { return this.facets.providers || [] } },
  watch: { query () { this.scheduleFilterLoad() }, group () { this.scheduleFilterLoad() }, provider () { this.scheduleFilterLoad() }, availability () { this.scheduleFilterLoad() } },
  created () { this.load() },
  beforeDestroy () { if (this.searchTimer) clearTimeout(this.searchTimer) },
  methods: {
    scheduleFilterLoad () { if (this.searchTimer) clearTimeout(this.searchTimer); this.pagination.page = 1; this.searchTimer = setTimeout(() => this.load(), 220) },
    async load () {
      try {
        const data = await api.models({ page: this.pagination.page, page_size: 12, query: this.query, group: this.group, provider: this.provider, availability: this.availability })
        this.models = data.models || []
        this.facets = data.filters || this.facets
        this.defaultModel = data.default_model || ''
        this.pagination = data.pagination || this.pagination
      } catch (e) { notify(e.message, 'error') }
    },
    changePage (page) { this.pagination.page = page; this.load() },
    reset () { this.query = ''; this.group = ''; this.provider = ''; this.availability = '' },
    currencySymbol (model) { return String(model.currency || 'CNY').toUpperCase() === 'USD' ? '$' : '¥' },
    displayPrice (model, side) { if (model[side] == null) return '未配置'; const value = Number(model[side]) * Number(model.pricing?.multiplier ?? 1); if (!Number.isFinite(value)) return '未配置'; return this.priceMode === 'token' ? (value / 1000).toFixed(5) : value.toFixed(4).replace(/\.?0+$/, '') || '0' },
    async copy (value) {
      const text = String(value || '')
      if (!text) return notify('暂无可复制内容', 'error')
      const copied = await copyToClipboard(text)
      notify(copied ? '已复制模型 ID' : '复制失败，请检查浏览器权限', copied ? 'success' : 'error')
    }
  }
}
</script>
