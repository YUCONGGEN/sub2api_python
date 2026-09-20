<template>
  <section class="proxy-admin">
    <div class="proxy-title">
      <div><span class="eyebrow">PROXY SUBSCRIPTIONS</span><h2>代理订阅管理</h2><p>管理订阅源、备用池大小和故障转移规则。查询和保存不会重启代理核心，也不会中断现有连接。</p></div>
      <button class="secondary-btn" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新状态' }}</button>
    </div>

    <div v-if="loadError" class="proxy-error"><strong>代理管理暂不可用</strong><span>{{ loadError }}</span><small>请在 application.yml 中启用并配置 rose.proxy-pool-admin。</small></div>
    <template v-else>
      <div class="proxy-status-grid">
        <article><span>代理核心</span><strong :class="snapshot.core && snapshot.core.online ? 'ok' : 'bad'">{{ snapshot.core && snapshot.core.online ? '运行中' : '离线' }}</strong><small>版本 {{ snapshot.core && snapshot.core.version || '未知' }}</small><button class="version-btn" :disabled="versionChecking" @click="checkVersion">{{ versionChecking ? '检测中…' : '检测更新' }}</button><small v-if="versionInfo">{{ versionInfo.update_available ? `发现新版本 ${versionInfo.latest}` : `已是最新版本 ${versionInfo.latest}` }}<a v-if="versionInfo.release_url" :href="versionInfo.release_url" target="_blank" rel="noopener">查看版本</a></small></article>
        <article><span>健康守护</span><strong :class="snapshot.monitor && snapshot.monitor.online ? 'ok' : 'bad'">{{ snapshot.monitor && snapshot.monitor.online ? '运行中' : '未更新' }}</strong><small>{{ snapshot.monitor && snapshot.monitor.group || '未配置代理组' }}</small></article>
        <article><span>当前节点</span><strong>{{ activeNode || '未知' }}</strong><small>{{ readyCount }} 个备用可用</small></article>
        <article><span>订阅来源</span><strong>{{ subscriptions.length }}</strong><small>URL 只加密保存在服务器</small></article>
      </div>

      <section class="proxy-card node-card">
        <div class="card-head"><div><span class="eyebrow">POOL STATUS</span><h3>节点状态</h3><small v-if="snapshot.monitor && snapshot.monitor.sync && snapshot.monitor.sync.synced_at">最近同步 {{ formatTime(snapshot.monitor.sync.synced_at) }} · {{ snapshot.monitor.sync.candidate_count || 0 }} 个候选节点</small></div><div class="node-head-actions"><span>{{ nodes.length }} 个健康池节点</span><button class="secondary-btn" :disabled="poolSyncing" @click="syncPool">{{ poolSyncing ? '同步中…' : '同步订阅节点' }}</button></div></div>
        <div class="node-grid"><article v-for="node in nodes" :key="node.name" :class="{ excluded: !node.eligible }"><i :class="node.status"></i><div><strong>{{ node.name }}</strong><span>{{ node.subscription_name || node.source || '未知来源' }} · {{ node.country || 'OTHER' }} · {{ node.eligible ? statusLabel(node.status) : '未参与当前策略' }}</span><small>当前连接 {{ node.active_connections || 0 }} 个 · 流量 {{ bytes(node.active_traffic_bytes || 0) }}</small><small>所属订阅共享：已用 {{ bytes(node.subscription_used_bytes) }} · 剩余 {{ bytes(node.subscription_remaining_bytes) }}</small></div><b>{{ node.delay_ms ? `${node.delay_ms} ms` : '—' }}</b></article></div>
      </section>

      <div class="proxy-grid">
        <section class="proxy-card subscription-source-card">
          <div class="card-head"><div><span class="eyebrow">SUBSCRIPTION SOURCES</span><h3>代理订阅</h3></div></div>
          <form class="source-form" @submit.prevent="addSource"><input v-model.trim="sourceForm.name" maxlength="80" placeholder="订阅名称" required /><input v-model.trim="sourceForm.url" type="url" autocomplete="off" placeholder="https://…/subscription" required /><button class="primary-btn" :disabled="sourceSaving">{{ sourceSaving ? '验证中…' : '验证并添加' }}</button></form>
          <p class="security-note">添加时直连验证订阅内容；页面和接口不会返回完整 URL。订阅源用于后续节点维护，只有已验证并加入下方代理池的节点才参与切换；删除来源不会强制断开已有连接。</p>
          <div v-if="subscriptions.length" class="source-list">
            <article v-for="item in subscriptions" :key="item.id">
              <div><strong>{{ item.name }}</strong><span>{{ item.host }} · 订阅 {{ item.node_count || 0 }} 个 · 当前池内 {{ item.pool_node_count || 0 }} 个</span><small>最近检查 {{ formatTime(item.last_checked_at) }}</small><em v-if="item.last_error">{{ item.last_error }}</em></div>
              <span :class="['source-state', sourceStateClass(item)]">{{ sourceStateLabel(item) }}</span>
              <div class="source-actions"><label class="source-limit"><span>健康节点上限</span><input v-model.number="item.max_healthy_nodes" type="number" min="1" max="20" /></label><button class="secondary-btn" :disabled="sourceUpdatingId === item.id" @click="saveSource(item)">保存</button><button class="secondary-btn" :disabled="sourceUpdatingId === item.id" @click="toggleSource(item)">{{ item.enabled ? '停用' : '启用' }}</button><button class="secondary-btn" :disabled="!item.checkable || checkingId === item.id" :title="item.checkable ? '重新下载并验证订阅' : '历史导入来源未保留订阅 URL'" @click="checkSource(item)">{{ checkingId === item.id ? '检查中…' : '检查' }}</button><button class="text-btn danger" @click="removeSource(item)">删除</button></div>
            </article>
          </div>
          <div v-else class="empty">尚未添加代理订阅</div>
        </section>

        <section class="proxy-card">
          <div class="card-head"><div><span class="eyebrow">FAILOVER POLICY</span><h3>备用池与切换规则</h3></div><button class="primary-btn" :disabled="policySaving" @click="savePolicy">{{ policySaving ? '保存中…' : '保存规则' }}</button></div>
          <div class="country-policy"><strong>可用国家或地区</strong><p>对所有已启用订阅统一生效；只有符合条件且已进入受控池的节点才参与切换。</p><div><label v-for="country in countryOptions" :key="country.code"><input v-model="policy.allowed_countries" type="checkbox" :value="country.code" /><span>{{ country.label }}</span></label></div></div>
          <div class="policy-form">
            <label v-for="field in policyFields" :key="field.key"><span>{{ field.label }}</span><input v-model.number="policy[field.key]" type="number" :min="limit(field.key, 'min')" :max="limit(field.key, 'max')" /><small>{{ field.help }}</small></label>
          </div>
          <p class="security-note">规则在守护程序下一轮检查时生效；至少连续 2 次失败/成功，避免节点抖动造成误切换。</p>
        </section>
      </div>

    </template>
  </section>
</template>

<script>
import { api } from '../api'
import { askConfirm, notify } from '../ui'

export default {
  data: () => ({
    loading: false,
    loadError: '',
    snapshot: {},
    policy: {},
    sourceForm: { name: '', url: '' },
    sourceSaving: false,
    policySaving: false,
    checkingId: '',
    sourceUpdatingId: '',
    versionChecking: false,
    versionInfo: null,
    poolSyncing: false,
    policyFields: [
      { key: 'standby_pool_size', label: '备用池大小', help: '当前节点之外最多参与故障切换的节点数' },
      { key: 'interval_seconds', label: '主节点检查间隔（秒）', help: '健康状态下每轮检查间隔' },
      { key: 'failure_confirmations', label: '失败确认次数', help: '连续失败达到该次数才切换' },
      { key: 'failure_interval_seconds', label: '失败复测间隔（秒）', help: '主节点失败后的复测间隔' },
      { key: 'replacement_confirmations', label: '备用成功确认次数', help: '备用节点连续成功后才能接管' },
      { key: 'replacement_interval_seconds', label: '备用复测间隔（秒）', help: '备用节点连续验证之间的等待时间' },
      { key: 'quarantine_seconds', label: '故障隔离时间（秒）', help: '失败节点暂时隔离时长' },
      { key: 'quarantine_recheck_seconds', label: '隔离重试间隔（秒）', help: '所有备用不可用时再次检查隔离节点' },
      { key: 'standby_probes_per_cycle', label: '每轮备用检查数', help: '0 表示健康时不检查备用节点' },
      { key: 'standby_interval_seconds', label: '备用复查间隔（秒）', help: '可用备用节点的再次检查间隔' },
      { key: 'recovery_successes', label: '恢复确认次数', help: '隔离节点连续成功多少次后恢复' },
      { key: 'recovery_interval_seconds', label: '恢复检查间隔（秒）', help: '故障节点首次恢复检查间隔' },
      { key: 'recovery_max_interval_seconds', label: '恢复检查最大间隔（秒）', help: '指数退避后的最大检查间隔' }
    ]
  }),
  computed: {
    subscriptions () { return this.snapshot.subscriptions || [] },
    nodes () { return this.snapshot.nodes || [] },
    countryOptions () { return this.snapshot.country_options || [] },
    activeNode () { return this.nodes.find(node => node.status === 'active')?.name || this.snapshot.monitor?.last_check?.node || '' },
    readyCount () { return this.nodes.filter(node => node.status === 'ready').length }
  },
  created () { this.load() },
  methods: {
    async load () {
      this.loading = true
      this.loadError = ''
      try {
        const data = await api.adminProxyPool()
        this.snapshot = data
        this.policy = { ...(data.policy || {}) }
        if (!Array.isArray(this.policy.allowed_countries)) this.policy.allowed_countries = (data.country_options || []).map(item => item.code)
      } catch (error) {
        this.loadError = error.message
      } finally {
        this.loading = false
      }
    },
    limit (key, side) { return this.snapshot.policy_limits?.[key]?.[side] },
    async savePolicy () {
      for (const field of this.policyFields) {
        const value = Number(this.policy[field.key])
        const minimum = Number(this.limit(field.key, 'min'))
        const maximum = Number(this.limit(field.key, 'max'))
        if (!Number.isInteger(value) || value < minimum || value > maximum) {
          const unit = field.key === 'standby_pool_size' || field.key.endsWith('confirmations') || field.key === 'standby_probes_per_cycle' || field.key === 'recovery_successes' ? '个' : '秒'
          notify(`${field.label.replace('（秒）', '')}必须在 ${minimum}～${maximum} ${unit}之间`, 'error')
          return
        }
      }
      this.policySaving = true
      try {
        const data = await api.updateProxyPoolPolicy(this.policy)
        this.snapshot = data
        this.policy = { ...(data.policy || {}) }
        notify('代理池规则已保存，将在下一轮检查时生效', 'success')
      } catch (error) { notify(error.message, 'error') } finally { this.policySaving = false }
    },
    async addSource () {
      this.sourceSaving = true
      try {
        await api.addProxySubscription(this.sourceForm)
        this.sourceForm = { name: '', url: '' }
        await this.load()
        notify('代理订阅验证通过并已加密保存', 'success')
      } catch (error) { notify(error.message, 'error') } finally { this.sourceSaving = false }
    },
    async checkSource (item) {
      if (!item.checkable) return
      this.checkingId = item.id
      try { await api.checkProxySubscription(item.id); await this.load(); notify('订阅检查完成', 'success') } catch (error) { notify(error.message, 'error') } finally { this.checkingId = '' }
    },
    async saveSource (item) {
      const maximum = Number(item.max_healthy_nodes)
      if (!Number.isInteger(maximum) || maximum < 1 || maximum > 20) {
        notify('每个订阅的健康节点上限必须在 1～20 个之间', 'error')
        return
      }
      this.sourceUpdatingId = item.id
      try { await api.updateProxySubscription(item.id, { max_healthy_nodes: maximum }); await this.load(); notify('订阅节点上限已保存', 'success') } catch (error) { notify(error.message, 'error') } finally { this.sourceUpdatingId = '' }
    },
    async toggleSource (item) {
      this.sourceUpdatingId = item.id
      try { await api.updateProxySubscription(item.id, { enabled: !item.enabled }); await this.load(); notify(item.enabled ? '订阅已停用' : '订阅已启用', 'success') } catch (error) { notify(error.message, 'error') } finally { this.sourceUpdatingId = '' }
    },
    async checkVersion () {
      this.versionChecking = true
      try { const data = await api.checkProxyCoreVersion(); this.versionInfo = data.version; notify(this.versionInfo.update_available ? `发现新版本 ${this.versionInfo.latest}` : '代理核心已是最新版本', this.versionInfo.update_available ? 'info' : 'success') } catch (error) { notify(error.message, 'error') } finally { this.versionChecking = false }
    },
    async syncPool () {
      this.poolSyncing = true
      try { await api.syncProxyPool(); await this.load(); notify('订阅节点已同步，新节点已加入，失效或已删除来源已移出', 'success') } catch (error) { notify(error.message, 'error') } finally { this.poolSyncing = false }
    },
    async removeSource (item) {
      if (!await askConfirm(`确定删除代理订阅「${item.name}」吗？已有连接不会被强制中断。`)) return
      try { await api.deleteProxySubscription(item.id); await this.load(); notify('代理订阅已删除', 'success') } catch (error) { notify(error.message, 'error') }
    },
    statusLabel (value) { return ({ active: '当前使用', ready: '备用可用', probation: '恢复确认中', quarantined: '已隔离', unknown: '待检查' })[value] || value },
    sourceStateLabel (item) { return !item.enabled ? '已停用' : item.status === 'validated' ? '可访问' : item.status === 'imported' ? '历史导入' : '异常' },
    sourceStateClass (item) { return !item.enabled ? 'disabled' : item.status === 'validated' ? 'ok' : item.status === 'imported' ? 'imported' : 'bad' },
    bytes (value) { const size = Number(value); if (!Number.isFinite(size)) return '未提供'; const units = ['B', 'KB', 'MB', 'GB', 'TB']; let current = Math.max(0, size); let index = 0; while (current >= 1024 && index < units.length - 1) { current /= 1024; index += 1 } return `${current.toFixed(index ? 2 : 0)} ${units[index]}` },
    formatTime (value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚未检查' }
  }
}
</script>

<style scoped>
.proxy-admin{display:grid;gap:16px}.proxy-title{display:flex;justify-content:space-between;align-items:flex-end;gap:20px}.proxy-title h2{margin:5px 0 4px;font:500 28px/1.2 'Playfair Display',Georgia,serif;color:#1f3652}.proxy-title p{margin:0;color:#6f8496;font-size:12px}.proxy-status-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.proxy-status-grid article,.proxy-card{border:1px solid #d7e4ee;border-radius:16px;background:#fff;padding:16px}.proxy-status-grid article{display:grid;gap:5px}.proxy-status-grid span{font:10px var(--mono);color:#7890a2}.proxy-status-grid strong{color:#1f3652;font-size:18px;overflow-wrap:anywhere}.proxy-status-grid small{color:#7b8d9c}.ok{color:#278a67!important}.bad{color:#bd5b55!important}.proxy-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.card-head{display:flex;align-items:center;justify-content:space-between;gap:15px;margin-bottom:13px}.card-head h3{margin:4px 0 0;color:#1f3652}.source-form{display:grid;grid-template-columns:minmax(140px,.6fr) minmax(220px,1.4fr) auto;gap:8px}.source-form input,.policy-form input{border:1px solid #cfdeea;border-radius:10px;background:#f8fbfd;color:#17334a;padding:11px}.security-note{margin:10px 0;color:#788c9c;font-size:11px;line-height:1.5}.source-list{display:grid;gap:8px}.source-list article{display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:10px;border:1px solid #dbe7ef;border-radius:12px;padding:11px}.source-list article>div:first-child{display:grid;gap:3px;min-width:0}.source-list strong{color:#213b52}.source-list span,.source-list small{color:#71889b;font-size:11px}.source-list em{color:#bd5b55;font-size:10px;overflow-wrap:anywhere}.source-state{border-radius:20px;background:#edf6f0;padding:5px 8px}.source-actions{display:flex;align-items:center;gap:7px}.policy-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.policy-form label{display:grid;gap:5px;color:#46667e;font-size:11px}.policy-form small{color:#8698a6;font-size:10px}.node-card{padding:16px}.node-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.node-grid article{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:9px;border:1px solid #dbe7ef;border-radius:11px;padding:11px}.node-grid i{width:9px;height:9px;border-radius:50%;background:#96a6b2}.node-grid i.active{background:#278a67;box-shadow:0 0 0 4px #278a6720}.node-grid i.ready{background:#4d8fb2}.node-grid i.quarantined{background:#bd5b55}.node-grid div{display:grid;gap:3px;min-width:0}.node-grid strong{color:#1f3652;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.node-grid span{color:#778d9d;font-size:10px}.node-grid b{color:#31576e;font:11px var(--mono)}.proxy-error{display:grid;gap:6px;border:1px solid #e4b9b5;border-radius:14px;background:#fff4f2;padding:18px;color:#9d4742}.proxy-error small{color:#a66a65}.empty{padding:28px;text-align:center;color:#8a9ba8}
.source-state.imported{color:#8a6b28!important;background:#fff6dd}.source-state.disabled{color:#71808a!important;background:#edf0f2}.version-btn{justify-self:start;padding:3px 0;border:0;background:transparent;color:#39728f;font-size:10px}.source-actions{flex-wrap:wrap;justify-content:flex-end}.source-limit{display:grid;gap:3px;color:#71889b;font-size:9px}.source-limit input{width:70px;min-height:34px;padding:6px 8px;border:1px solid #cfdeea;border-radius:8px}.country-policy{margin-bottom:12px;padding:12px;border:1px solid #dbe7ef;border-radius:12px;background:#f8fbfd}.country-policy>strong{color:#29485f;font-size:12px}.country-policy>p{margin:4px 0 9px;color:#788c9c;font-size:10px}.country-policy>div{display:flex;flex-wrap:wrap;gap:7px}.country-policy label{display:flex;align-items:center;gap:5px;padding:6px 8px;border:1px solid #d5e3eb;border-radius:8px;background:#fff;color:#547086;font-size:10px}.country-policy input{margin:0}.node-head-actions{display:flex;align-items:center;gap:10px}.node-grid small{display:block;color:#7890a0;font-size:9px}.node-grid article.excluded{opacity:.58}
@media(max-width:1000px){.proxy-status-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.proxy-grid{grid-template-columns:1fr}.node-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:680px){.proxy-title{align-items:flex-start;flex-direction:column}.proxy-status-grid,.policy-form,.node-grid{grid-template-columns:1fr}.source-form{grid-template-columns:1fr}.source-list article{grid-template-columns:1fr}.source-actions{justify-content:flex-end}}
</style>
