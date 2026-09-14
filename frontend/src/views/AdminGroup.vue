<template>
  <section class="page admin-group-detail">
    <div class="page-head">
      <div><span class="eyebrow">ADMIN / GROUP DETAIL</span><h1>{{ detail ? detail.group.name : '分组详情' }}</h1><p>成员名单与累计使用情况</p></div>
      <router-link class="secondary-btn" to="/admin#admin-business-groups">返回用户分组</router-link>
    </div>
    <div v-if="error" class="panel group-detail-feedback" role="alert"><p>{{ error }}</p><button class="secondary-btn" @click="load(page)">重新加载</button></div>
    <div v-else-if="loading" class="panel group-detail-feedback" role="status">正在加载分组成员与用量…</div>
    <template v-else-if="detail">
      <div class="group-detail-metrics">
        <div class="panel"><span class="eyebrow">GROUP MEMBERS</span><h2>{{ number(detail.totals.member_count) }} <small>人</small></h2><p>当前基础成员</p></div>
        <div class="panel"><span class="eyebrow">TOTAL SPEND</span><h2>¥{{ money(detail.totals.total_cost) }}</h2><p>累计调用费用</p></div>
        <div class="panel"><span class="eyebrow">TOTAL TOKENS</span><h2>{{ number(detail.totals.total_tokens) }}</h2><p>累计 Token</p></div>
        <div class="panel"><span class="eyebrow">TOTAL REQUESTS</span><h2>{{ number(detail.totals.requests) }}</h2><p>累计请求</p></div>
      </div>
      <div class="panel group-detail-policy">
        <span class="status">权重 {{ detail.group.weight }}</span><span class="status">每人并发 {{ detail.group.concurrency_limit }}</span>
        <span v-for="model in detail.group.allowed_models" :key="model" class="status">{{ model === '*' ? '全部模型' : model }}</span>
        <span v-if="!detail.group.allowed_models.length" class="status">未开放模型</span>
        <p>{{ detail.group.description || '暂无分组说明' }}</p>
      </div>
      <div class="panel">
        <div class="panel-head"><div><span class="eyebrow">MEMBER DIRECTORY</span><h2>组内人员</h2></div><button class="secondary-btn" @click="load(page)">刷新</button></div>
        <p class="panel-note">按当前基础分组成员汇总全部历史调用，包含已停用成员；不含已删除成员。成员换组后，累计用量随成员归属变化；套餐临时升级不改变基础分组。费用为调用计费金额，不等同于钱包扣款。</p>
        <div v-if="detail.members.items.length" class="table-wrap">
          <table><thead><tr><th>账户</th><th>状态</th><th>累计花费</th><th>累计 Token</th><th>请求数</th><th>最近登录</th><th>操作</th></tr></thead>
            <tbody><tr v-for="member in detail.members.items" :key="member.id">
              <td><router-link :to="`/admin/users/${member.id}`">@{{ member.username }}</router-link><small>{{ member.email || '未设置邮箱' }}</small></td>
              <td><span :class="['status', member.enabled ? 'success' : 'pending']">{{ member.enabled ? '正常' : '停用' }}</span></td>
              <td class="group-detail-value">¥{{ money(member.total_cost) }}</td><td class="group-detail-value">{{ number(member.total_tokens) }}</td><td>{{ number(member.requests) }}</td>
              <td>{{ date(member.last_login) }}</td><td><router-link class="text-btn" :to="`/admin/users/${member.id}`">查看用户</router-link></td>
            </tr></tbody>
          </table>
        </div>
        <div v-else class="empty compact-empty">该分组暂无成员</div>
        <div v-if="detail.members.pages > 1" class="pagination"><button class="secondary-btn" :disabled="page <= 1" @click="load(page - 1)">上一页</button><span>第 {{ page }} / {{ detail.members.pages }} 页 · 共 {{ detail.members.total }} 人</span><button class="secondary-btn" :disabled="page >= detail.members.pages" @click="load(page + 1)">下一页</button></div>
      </div>
    </template>
  </section>
</template>

<script>
import { api } from '../api'

export default {
  props: { id: { type: String, required: true } },
  data: () => ({ detail: null, loading: true, error: '', page: 1, requestVersion: 0 }),
  watch: { id: { immediate: true, handler() { this.load(1) } } },
  beforeDestroy() { this.requestVersion++ },
  methods: {
    async load(page = 1) {
      const version = ++this.requestVersion
      this.loading = true
      this.error = ''
      try {
        const data = await api.userGroupDetail(this.id, { page, page_size: 5 })
        if (version !== this.requestVersion) return
        this.detail = data
        this.page = data.members.page
      } catch (error) {
        if (version === this.requestVersion) {
          this.detail = null
          this.error = error.message || '分组详情加载失败，请重试'
        }
      } finally {
        if (version === this.requestVersion) this.loading = false
      }
    },
    number(value) { return Number(value || 0).toLocaleString('zh-CN') },
    money(value) { return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 4, maximumFractionDigits: 4 }) },
    date(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '从未登录' }
  }
}
</script>

<style scoped>
.admin-group-detail > .page-head > div:first-child { display: block; }
.admin-group-detail > .panel, .group-detail-metrics { margin-bottom: 20px; }
.admin-group-detail td small { display: block; margin-top: 5px; color: var(--muted); }
.admin-group-detail td a { color: inherit; text-decoration: none; }
.admin-group-detail td a:hover { text-decoration: underline; }
.group-detail-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; }
.group-detail-metrics .panel { min-width: 0; }
.group-detail-metrics h2 { font-size: clamp(22px, 2.2vw, 34px); overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.group-detail-metrics h2 small { font-size: 14px; }
.group-detail-metrics p { margin-bottom: 0; color: var(--muted); }
.group-detail-policy { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.group-detail-policy p { flex-basis: 100%; margin: 0; color: var(--muted); }
.group-detail-value { white-space: nowrap; font-variant-numeric: tabular-nums; }
.group-detail-feedback { min-height: 140px; display: flex; gap: 20px; align-items: center; justify-content: center; }
@media (max-width: 1000px) { .group-detail-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 480px) { .group-detail-metrics { gap: 10px; } .group-detail-metrics .panel { padding: 16px; } .group-detail-metrics .eyebrow { font-size: 10px; } }
</style>
