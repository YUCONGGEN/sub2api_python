<template>
  <section class="page user-group-overview">
    <div class="page-head">
      <div>
        <span class="eyebrow">MY ACCESS GROUP</span>
        <h1>{{ detail ? detail.group.name : '我的分组' }}</h1>
        <p>查看当前生效分组的访问策略、汇总用量和成员使用数据。</p>
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
        <p class="privacy-note">组内成员数据仅供查看，不提供用户详情入口、编辑、停用或其他管理操作。</p>
      </div>

      <div v-if="detail.members" class="panel group-member-directory">
        <div class="panel-head"><div><span class="eyebrow">MEMBER DIRECTORY</span><h2>组内人员用量</h2></div><span class="status">只读</span></div>
        <p class="member-note">展示当前基础分组成员的累计调用数据；凭据、密码、API Key、钱包和管理入口不会显示。</p>
        <div v-if="detail.members.items.length" class="table-wrap">
          <table><thead><tr><th>账户</th><th>状态</th><th>累计花费</th><th>累计 Token</th><th>请求数</th><th>最近登录</th></tr></thead>
            <tbody><tr v-for="member in detail.members.items" :key="member.id">
              <td><strong>@{{ member.username }}</strong><small>{{ member.email || '未设置邮箱' }}</small></td>
              <td><span :class="['status', member.enabled ? 'success' : 'pending']">{{ member.enabled ? '正常' : '停用' }}</span></td>
              <td class="member-value">¥{{ money(member.total_cost) }}</td><td class="member-value">{{ number(member.total_tokens) }}</td><td>{{ number(member.requests) }}</td><td>{{ date(member.last_login) }}</td>
            </tr></tbody>
          </table>
        </div>
        <div v-else class="empty compact-empty">该分组暂无成员</div>
        <div v-if="detail.members.pages > 1" class="pagination"><button class="secondary-btn" :disabled="page <= 1 || loading" @click="load(page - 1)">上一页</button><span>第 {{ page }} / {{ detail.members.pages }} 页 · 共 {{ detail.members.total }} 人</span><button class="secondary-btn" :disabled="page >= detail.members.pages || loading" @click="load(page + 1)">下一页</button></div>
      </div>
    </template>
  </section>
</template>

<script>
import { api } from '../api'

export default {
  name: 'UserGroup',
  data: () => ({ detail: null, loading: true, error: '', page: 1 }),
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
    async load (page = this.page) {
      this.loading = true
      this.error = ''
      try { this.detail = await api.myGroupOverview({ page, page_size: 5 }); this.page = this.detail?.members?.page || 1 } catch (error) { this.detail = null; this.error = error.message || '分组概况加载失败' } finally { this.loading = false }
    },
    number (value) { return Number(value || 0).toLocaleString('zh-CN') },
    money (value) { return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 4, maximumFractionDigits: 4 }) },
    date (value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '从未登录' }
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
.group-member-directory { margin-top:20px; }
.member-note { color:var(--muted); }
.group-member-directory td small { display:block;margin-top:5px;color:var(--muted); }
.member-value { white-space:nowrap;font-variant-numeric:tabular-nums; }
.group-overview-feedback { min-height:160px;display:flex;align-items:center;justify-content:center;gap:18px; }
@media (max-width:1000px) { .group-overview-metrics { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:680px) { .policy-facts { grid-template-columns:1fr; } }
</style>
