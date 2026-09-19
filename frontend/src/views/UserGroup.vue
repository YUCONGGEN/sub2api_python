<template>
  <section class="page user-group-overview">
    <div class="page-head">
      <div>
        <span class="eyebrow">MY ACCESS GROUP</span>
        <h1>{{ detail ? detail.group.name : '我的分组' }}</h1>
        <p>查看所在分组的汇总用量和成员使用数据。</p>
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
.group-overview-metrics p { color:var(--muted); }
.group-member-directory { margin-top:20px; }
.member-note { color:var(--muted); }
.group-member-directory td small { display:block;margin-top:5px;color:var(--muted); }
.member-value { white-space:nowrap;font-variant-numeric:tabular-nums; }
.group-overview-feedback { min-height:160px;display:flex;align-items:center;justify-content:center;gap:18px; }
@media (max-width:1000px) { .group-overview-metrics { grid-template-columns:repeat(2,minmax(0,1fr)); } }
</style>
