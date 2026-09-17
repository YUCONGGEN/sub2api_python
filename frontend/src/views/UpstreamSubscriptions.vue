<template>
  <section class="page subscription-page">
    <div class="page-head">
      <div>
        <div class="eyebrow">SHARED SUBSCRIPTION POOL</div>
        <h1>共享订阅账号池</h1>
        <p>{{ isAdmin ? '管理全部共享订阅账号与用户提交的配置申请。' : '可查看全部共享账号；你只能维护自己添加的账号，凭据与运行状态不会向其他用户公开。' }}</p>
      </div>
      <button class="primary-action" type="button" @click="openCreate">添加订阅账号</button>
    </div>

    <div v-if="isAdmin" class="metric-grid gateway-metrics">
      <div class="metric-card"><span>账号总数</span><strong>{{ summary.total || 0 }}</strong><small>全部分页与供应商</small></div>
      <div class="metric-card"><span>OpenAI</span><strong>{{ providerCount('openai') }}</strong><small>Responses / Codex</small></div>
      <div class="metric-card"><span>Claude</span><strong>{{ providerCount('claude') }}</strong><small>Anthropic Messages</small></div>
      <div class="metric-card" :class="{ highlight: gatewayEnabled }"><span>网关状态</span><strong>{{ gatewayEnabled ? '已启用' : '已停用' }}</strong><small>凭据全程加密保存</small></div>
    </div>
    <div v-if="isAdmin" class="gateway-strip gateway-capacity" aria-label="订阅账号池实时容量">
      <span><small>正在执行</small><b>{{ gatewayMetrics.active_requests || 0 }}</b></span>
      <span><small>排队请求</small><b>{{ gatewayMetrics.queue_waiting || 0 }} / {{ gatewayMetrics.queue_limit || 0 }}</b></span>
      <span><small>RPM 使用</small><b>{{ gatewayMetrics.rpm_used || 0 }} / {{ gatewayMetrics.rpm_capacity || 0 }}</b></span>
      <span><small>账号冷却</small><b>{{ gatewayMetrics.cooldown_accounts || 0 }} / {{ gatewayMetrics.account_pool_total || 0 }}</b></span>
      <span><small>本地限流</small><b>{{ gatewayMetrics.local_rate_limits || 0 }}</b></span>
      <span><small>上游容量不足</small><b>{{ gatewayMetrics.upstream_capacity_failures || 0 }}</b></span>
      <span><small>排队拒绝</small><b>{{ gatewayMetrics.queue_rejected || 0 }}</b></span>
    </div>
    <GatewayActivity v-if="isAdmin" :gateway="gatewayMetrics" />

    <section v-if="showForm" class="panel account-editor">
      <div class="panel-head">
        <div><span class="eyebrow">{{ editingId ? 'EDIT ACCOUNT' : 'ADD ACCOUNT' }}</span><h2>{{ editingId ? '编辑订阅账号' : '添加订阅账号' }}</h2></div>
        <button class="text-action" type="button" @click="closeForm">关闭</button>
      </div>

      <div class="mode-tabs" v-if="!editingId">
        <button :class="{ active: form.mode === 'oauth' }" @click="form.mode = 'oauth'">OAuth 授权</button>
        <button :class="{ active: form.mode === 'manual' }" @click="form.mode = 'manual'">导入 Token</button>
      </div>

      <div class="form-grid">
        <label><span>供应商</span><select v-model="form.provider" :disabled="!!editingId || !!oauthSession"><option value="openai">OpenAI / Codex</option><option value="claude">Claude / Anthropic</option></select></label>
        <label><span>显示名称</span><input v-model.trim="form.name" maxlength="120" placeholder="例如：我的 ChatGPT Pro" /></label>
        <label class="span-2"><span>可调度模型（逗号分隔）</span><textarea v-model="form.models" rows="3" placeholder="gpt-5.4, gpt-5.4-mini"></textarea></label>
        <label v-if="isAdmin"><span>优先级</span><input v-model.number="form.priority" type="number" min="-1000" max="1000" /></label>
        <label v-if="isAdmin"><span>调度权重</span><input v-model.number="form.weight" type="number" min="1" max="100" /></label>
        <label v-if="isAdmin"><span>输入价（¥/百万 Token）</span><input v-model.number="form.input_price_cny" type="number" min="0" step="0.01" /></label>
        <label v-if="isAdmin"><span>输出价（¥/百万 Token）</span><input v-model.number="form.output_price_cny" type="number" min="0" step="0.01" /></label>
        <label v-if="isAdmin"><span>价格倍率</span><input v-model.number="form.price_multiplier" type="number" min="0" step="0.01" /></label>
        <label class="check-line"><input v-model="form.enabled" type="checkbox" /><span>启用此账号</span></label>
      </div>

      <template v-if="!editingId && form.mode === 'manual'">
        <div class="form-grid token-grid">
          <label class="span-2"><span>Access Token / Codex auth.json</span><textarea v-model.trim="form.access_token" rows="5" autocomplete="off" placeholder="可粘贴 Codex auth.json，或其中 tokens.access_token；不要粘贴浏览器 Session Token"></textarea><small>完整 auth.json 会自动提取 access_token、refresh_token 与账号 ID；凭据由服务端加密保存。</small></label>
          <label class="span-2"><span>Refresh Token（可选）</span><textarea v-model.trim="form.refresh_token" rows="2" autocomplete="off"></textarea></label>
          <label><span>过期时间（可选）</span><input v-model.trim="form.expires_at" placeholder="2026-12-31T00:00:00Z" /></label>
          <label><span>账号邮箱（可选）</span><input v-model.trim="form.email" type="email" /></label>
        </div>
      </template>

      <template v-if="editingId">
        <details class="credential-update">
          <summary>更新 Token（留空则保持原凭据）</summary>
          <div class="form-grid token-grid">
            <label class="span-2"><span>新 Access Token / Codex auth.json</span><textarea v-model.trim="form.access_token" rows="4" autocomplete="off" placeholder="可粘贴完整 Codex auth.json；留空则保持原凭据"></textarea><small>浏览器的 5 段 JWE Session Token 不能用于 Codex API。</small></label>
            <label class="span-2"><span>新 Refresh Token</span><textarea v-model.trim="form.refresh_token" rows="2" autocomplete="off"></textarea></label>
          </div>
        </details>
      </template>

      <label v-if="!editingId" class="compliance-check"><input v-model="form.compliance_confirmed" type="checkbox" /><span>我确认已获得此账号所有者授权，并会遵守 OpenAI / Anthropic 的服务条款、地区限制和账号共享规则。</span></label>

      <div v-if="oauthSession && !editingId" class="oauth-step">
        <strong>授权链接已生成</strong>
        <p>在新窗口完成授权后，把浏览器最终回调地址或页面显示的完整授权码粘贴到下方。</p>
        <a :href="oauthSession.authorization_url" target="_blank" rel="noopener">打开 {{ providerLabel(form.provider) }} 授权页面 ↗</a>
        <textarea v-model.trim="form.callback_value" rows="3" placeholder="OpenAI：粘贴 http://localhost:1455/auth/callback?...；Claude：粘贴 code#state"></textarea>
      </div>

      <div class="editor-actions">
        <button v-if="editingId" class="primary-action" :disabled="busy" @click="saveEdit">{{ busy ? '保存中…' : '保存修改' }}</button>
        <button v-else-if="form.mode === 'manual'" class="primary-action" :disabled="busy" @click="saveManual">{{ busy ? '正在验证连通性…' : '验证并加入共享池' }}</button>
        <button v-else-if="!oauthSession" class="primary-action" :disabled="busy" @click="startOAuth">{{ busy ? '生成中…' : '生成 OAuth 授权链接' }}</button>
        <button v-else class="primary-action" :disabled="busy || !form.callback_value" @click="finishOAuth">{{ busy ? '交换 Token 中…' : '完成授权并保存' }}</button>
        <button class="secondary-action" type="button" @click="closeForm">取消</button>
      </div>
    </section>

    <section class="panel accounts-panel">
      <div class="panel-head">
        <div><span class="eyebrow">ACCOUNT POOL</span><h2>上游账号池</h2></div>
        <div class="provider-filter"><button :class="['secondary-btn', { active: filter === '' }]" @click="setFilter('')">全部</button><button :class="['secondary-btn', { active: filter === 'openai' }]" @click="setFilter('openai')">OpenAI</button><button :class="['secondary-btn', { active: filter === 'claude' }]" @click="setFilter('claude')">Claude</button></div>
      </div>
      <div v-if="error" class="data-error" role="alert"><strong>订阅账号加载失败</strong><span>{{ error }}</span><button class="secondary-btn" @click="load">重试</button></div>
      <div v-if="loading" class="empty">正在加载订阅账号…</div>
      <div v-else-if="!accounts.length" class="empty">暂无订阅账号。添加后，对应模型会自动进入 `/v1/models`。</div>
      <div v-else class="account-list">
        <article v-for="account in accounts" :key="account.id" :class="['account-card', account.provider]">
          <div class="account-card-top">
            <div class="account-main">
              <div class="provider-mark" :class="account.provider">{{ account.provider === 'openai' ? 'O' : 'C' }}</div>
              <div class="account-identity">
                <span class="account-id">{{ account.provider.toUpperCase() }} ACCOUNT {{ String(account.id).padStart(2, '0') }}</span>
                <h3>{{ account.name }}</h3>
              </div>
            </div>
            <span class="status-chip" :class="statusClass(account)">{{ statusText(account) }}</span>
          </div>
          <p class="account-summary">{{ providerLabel(account.provider) }} · {{ account.owner_label || '系统账号' }}<br>{{ account.can_manage ? (account.email || '未提供邮箱') + ' · ' + account.credential_mask : '凭据仅账号所有者和管理员可见' }}</p>
          <div class="account-models"><span v-for="model in account.models" :key="model">{{ model }}</span></div>
          <dl v-if="account.can_manage" class="account-facts"><div v-if="isAdmin"><dt>优先级 / 权重</dt><dd>{{ account.priority }} / {{ account.weight }}</dd></div><div><dt>错误次数</dt><dd>{{ account.error_count || 0 }}</dd></div><div><dt>Token 过期</dt><dd>{{ displayTime(account.expires_at) }}</dd></div><div><dt>最近使用</dt><dd>{{ displayTime(account.last_used_at) }}</dd></div></dl>
          <div v-if="account.provider === 'openai' && account.can_manage" class="account-quota">
            <div class="account-quota-head"><div><small>CODEX SUBSCRIPTION</small><strong>订阅剩余量</strong></div><button type="button" :disabled="quotaState(account).loading" @click="loadAccountQuota(account, true)">{{ quotaState(account).loading ? '查询中…' : '刷新' }}</button></div>
            <div v-if="quotaState(account).loading && !quotaState(account).quota" class="quota-loading">正在安全查询订阅窗口…</div>
            <div v-else-if="quotaState(account).error && !quotaState(account).quota" class="quota-error">{{ quotaState(account).error }}</div>
            <template v-else-if="quotaState(account).quota">
              <div class="quota-plan"><span>{{ quotaState(account).quota.plan_type || '订阅套餐' }}</span><em :class="quotaState(account).quota.limit_reached ? 'limited' : ''">{{ quotaState(account).quota.limit_reached ? '已到上限' : '可用' }}</em></div>
              <div class="quota-window-list">
                <div v-for="item in quotaWindows(quotaState(account).quota)" :key="item.label + item.limit_window_seconds" class="quota-window">
                  <div><span>{{ item.label }}</span><b>剩余 {{ quotaPercent(item.remaining_percent) }}%</b></div>
                  <i><u :style="{ width: quotaPercent(item.used_percent) + '%' }"></u></i>
                  <small>已用 {{ quotaPercent(item.used_percent) }}% · {{ item.reset_at ? displayTime(item.reset_at) + ' 重置' : '重置时间未知' }}</small>
                </div>
                <div v-if="!quotaWindows(quotaState(account).quota).length" class="quota-empty">上游暂未返回用量窗口</div>
              </div>
              <div class="quota-reset"><span><small>可用重置次数</small><b>{{ resetCreditCount(quotaState(account).quota) }}</b></span><span><small>最近查询</small><b>{{ displayTime(quotaState(account).quota.fetched_at) }}</b></span></div>
              <p v-if="quotaState(account).quota.warning" class="quota-warning">{{ quotaState(account).quota.warning }}</p>
            </template>
          </div>
          <p v-if="account.can_manage && account.last_error" class="account-error">{{ account.last_error }}</p>
          <div v-if="account.can_manage" class="account-actions"><button class="secondary-btn" :disabled="actionId === account.id" @click="testAccount(account)">测试</button><button v-if="account.has_refresh_token" class="secondary-btn" :disabled="actionId === account.id" @click="refreshAccount(account)">刷新 Token</button><button class="secondary-btn" @click="openEdit(account)">编辑</button><button class="secondary-btn" @click="toggleAccount(account)">{{ account.enabled ? '停用' : '启用' }}</button><button class="text-btn danger" @click="removeAccount(account)">删除</button></div>
        </article>
      </div>
      <div class="pagination" v-if="pagination.pages > 1"><button class="secondary-btn" :disabled="loading || pagination.page <= 1" @click="changePage(pagination.page - 1)">上一页</button><span>第 {{ pagination.page }} / {{ pagination.pages }} 页，共 {{ pagination.total }} 个账号</span><button class="secondary-btn" :disabled="loading || pagination.page >= pagination.pages" @click="changePage(pagination.page + 1)">下一页</button></div>
    </section>

    <section class="panel config-requests">
      <div class="panel-head"><div><span class="eyebrow">UPSTREAM CONFIG REQUEST</span><h2>上游配置申请</h2><p>提交 URL、API Key 和模型 ID 给管理员，API Key 会加密保存且列表中只显示掩码。</p></div></div>
      <div class="request-compose">
        <input v-model.trim="requestForm.url" type="url" placeholder="https://api.example.com/v1" />
        <input v-model.trim="requestForm.api_key" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="API Key（输入内容可见）" />
        <input v-model.trim="requestForm.model_id" placeholder="model-id" />
        <label class="proxy-choice"><input v-model="requestForm.use_proxy" type="checkbox" /><span>走服务器代理</span></label>
        <button class="primary-action" :disabled="requestBusy" @click="submitConfigRequest">{{ requestBusy ? '发送中…' : '发送给管理员' }}</button>
      </div>
      <div v-if="requestLoading" class="empty">正在加载申请…</div>
      <div v-else-if="!configRequests.length" class="empty">暂无配置申请</div>
      <div v-else class="request-list">
        <article v-for="item in configRequests" :key="item.id">
          <div><strong>{{ item.model_id }}</strong><small v-if="isAdmin">用户 #{{ item.user_id }}</small><small>{{ item.base_url }}</small></div>
          <code>{{ item.api_key_mask }}</code><span class="proxy-badge">{{ item.use_proxy ? '走代理' : '直连' }} · trust-env {{ item.use_proxy ? 'true' : 'false' }}</span><span :class="['request-status', String(item.status).toLowerCase()]">{{ requestStatus(item.status) }}</span>
          <div v-if="isAdmin" class="request-actions"><button @click="copyConfigRequest(item)">复制配置</button><button @click="setRequestStatus(item, 'ACCEPTED')">已处理</button><button @click="setRequestStatus(item, 'REJECTED')">拒绝</button></div>
        </article>
      </div>
    </section>

    <section class="panel endpoint-help">
      <div class="panel-head"><div><span class="eyebrow">COMPATIBLE ENDPOINTS</span><h2>调用入口</h2></div></div>
      <div class="endpoint-grid"><div><strong>OpenAI Subscription</strong><code>POST /v1/chat/completions</code><code>POST /v1/responses</code><p>两种格式统一接入同一订阅账号池；Chat Completions 会在后端自动转换。客户端填写服务根地址 /v1，同一对话可传稳定的 prompt_cache_key 保持账号粘连。</p></div><div><strong>Claude Subscription</strong><code>POST /v1/chat/completions</code><code>POST /v1/messages</code><code>POST /v1/messages/count_tokens</code><p>Claude 模型也可统一使用 Chat Completions，后端自动转换 Anthropic Messages；prompt_cache_key 或 metadata.user_id 可作为会话粘连键。</p></div></div>
      <p class="safety-note">账号池按优先级和平滑权重轮询，失败、冷却或限流时自动切换下一账号；同时采用单账号限并发、RPM 上限、退避与健康账号粘连，不伪造浏览器或人为操作，也不能保证上游账号不会被限制。</p>
    </section>
  </section>
</template>

<script>
import { api } from '../api'
import { askConfirm, notify } from '../ui'
import GatewayActivity from '../components/GatewayActivity.vue'

const defaults = provider => ({ mode: 'oauth', provider, name: '', models: provider === 'openai' ? 'gpt-5.4, gpt-5.4-mini' : 'claude-sonnet-4-6, claude-opus-4-6', priority: 0, weight: 1, input_price_cny: 0, output_price_cny: 0, price_multiplier: 1, enabled: true, access_token: '', refresh_token: '', expires_at: '', email: '', callback_value: '', compliance_confirmed: false })

export default {
  name: 'UpstreamSubscriptions',
  components: { GatewayActivity },
  props: { user: { type: Object, default: null }, subscriptionContributionsEnabled: { type: Boolean, default: false }, subscriptionConfigRequestDefaultUseProxy: { type: Boolean, default: false } },
  data: () => ({ accounts: [], summary: {}, gatewayMetrics: {}, quotaByAccount: {}, pagination: { page: 1, pages: 1, total: 0 }, error: '', loading: false, busy: false, actionId: null, filter: '', showForm: false, editingId: null, oauthSession: null, gatewayEnabled: false, metricsTimer: null, configRequests: [], requestLoading: false, requestBusy: false, requestForm: { url: '', api_key: '', model_id: '', use_proxy: false }, form: defaults('openai') }),
  computed: { isAdmin () { return String(this.user?.role || '').toUpperCase() === 'ADMIN' } },
  watch: {
    'form.provider' (next, previous) { if (!this.editingId && !this.oauthSession && next !== previous) this.form.models = defaults(next).models },
    subscriptionConfigRequestDefaultUseProxy (value) {
      if (!this.requestForm.url && !this.requestForm.api_key && !this.requestForm.model_id) this.requestForm.use_proxy = value
    }
  },
  created () { this.requestForm.use_proxy = this.subscriptionConfigRequestDefaultUseProxy; this.load(); this.loadConfigRequests(); if (this.isAdmin) this.metricsTimer = window.setInterval(this.refreshGatewayMetrics, 5000) },
  beforeUnmount () { window.clearInterval(this.metricsTimer) },
  beforeDestroy () { window.clearInterval(this.metricsTimer) },
  methods: {
    providerLabel (provider) { return provider === 'openai' ? 'OpenAI / Codex' : 'Claude / Anthropic' },
    providerCount (provider) { return Number(this.summary[provider] || 0) },
    quotaState (account) { return this.quotaByAccount[account.id] || { loading: false, quota: null, error: '' } },
    quotaWindows (quota) { return [quota?.short_window, quota?.long_window].filter(Boolean) },
    quotaPercent (value) { return Math.min(100, Math.max(0, Number(value || 0))).toFixed(1).replace(/\.0$/, '') },
    resetCreditCount (quota) { const value = quota?.reset_credits?.available_count; return value === null || value === undefined ? '未提供' : `${Number(value).toLocaleString('zh-CN')} 次` },
    setQuotaState (accountId, value) { this.quotaByAccount = { ...this.quotaByAccount, [accountId]: value } },
    async loadAccountQuota (account, refresh = false, quiet = false) {
      if (account.provider !== 'openai') return
      const previous = this.quotaState(account)
      this.setQuotaState(account.id, { ...previous, loading: true, error: '' })
      try {
        const data = await api.upstreamSubscriptionQuota(account.id, refresh)
        const quota = data.quota || null
        if (quota?.account_disabled) {
          account.enabled = false
          account.status = 'DISABLED'
          account.last_error = quota.warning || '每周订阅剩余量低于阈值，已停用并等待管理员处理'
        }
        this.setQuotaState(account.id, { loading: false, quota, error: '' })
        if (refresh && !quiet) notify(`${account.name}：订阅用量已刷新`, 'success')
      } catch (error) {
        const message = error.message || '订阅用量查询失败'
        this.setQuotaState(account.id, { loading: false, quota: previous.quota || null, error: message })
        if (refresh && !quiet) notify(message, 'error')
      }
    },
    loadAccountQuotas () { this.accounts.filter(account => account.provider === 'openai' && account.can_manage).forEach(account => this.loadAccountQuota(account, true, true)) },
    statusClass (account) { return account.enabled ? String(account.status || 'READY').toLowerCase() : 'disabled' },
    statusText (account) { if (!account.enabled) return '已停用'; return ({ READY: '可用', INVALID: '凭据失效', COOLDOWN: '冷却中', DISABLED: '已停用' })[account.status] || account.status },
    displayTime (value) { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN') },
    payload () { return { provider: this.form.provider, name: this.form.name, models: this.form.models, priority: this.form.priority, weight: this.form.weight, input_price_cny: this.form.input_price_cny, output_price_cny: this.form.output_price_cny, price_multiplier: this.form.price_multiplier, enabled: this.form.enabled, compliance_confirmed: this.form.compliance_confirmed } },
    async load () { this.loading = true; this.error = ''; try { const data = await api.upstreamSubscriptions({ provider: this.filter, page: this.pagination.page, page_size: 12 }); this.accounts = data.accounts || []; this.summary = data.summary || {}; this.gatewayMetrics = data.gateway_metrics || {}; this.pagination = data.pagination || this.pagination; this.gatewayEnabled = !!data.gateway_enabled; this.loadAccountQuotas() } catch (error) { this.error = error.message || '请检查后端服务后重试'; notify(this.error, 'error') } finally { this.loading = false } },
    async refreshGatewayMetrics () { if (!this.isAdmin) return; try { const data = await api.upstreamGatewayMetrics(); this.gatewayMetrics = data.gateway_metrics || this.gatewayMetrics } catch (error) {} },
    requestStatus (status) { return ({ PENDING: '待处理', ACCEPTED: '已处理', REJECTED: '已拒绝' })[status] || status },
    async loadConfigRequests () { this.requestLoading = true; try { const data = await api.upstreamConfigRequests({ page: 1, page_size: 50 }); this.configRequests = data.requests || [] } catch (error) { notify(error.message || '配置申请加载失败', 'error') } finally { this.requestLoading = false } },
    async submitConfigRequest () { if (!this.requestForm.url || !this.requestForm.api_key || !this.requestForm.model_id) return notify('请完整填写 URL、API Key 和模型 ID', 'error'); this.requestBusy = true; try { const data = await api.createUpstreamConfigRequest(this.requestForm); notify(data.message || '配置申请已发送', 'success'); this.requestForm = { url: '', api_key: '', model_id: '', use_proxy: this.subscriptionConfigRequestDefaultUseProxy }; await this.loadConfigRequests() } catch (error) { notify(error.message, 'error') } finally { this.requestBusy = false } },
    async copyConfigRequest (item) { try { const data = await api.revealUpstreamConfigRequest(item.id); const value = `- id: ${data.request.model_id}\n  enabled: true\n  provider: OpenAI\n  endpoint: Chat\n  upstream-model: ${data.request.model_id}\n  base-url: ${data.request.url}\n  api-key: ${data.request.api_key}\n  trust-env: ${data.request.use_proxy ? 'true' : 'false'}`; await navigator.clipboard.writeText(value); notify('可粘贴到 rose.models 的 YAML 配置已复制', 'success') } catch (error) { notify(error.message || '复制失败', 'error') } },
    async setRequestStatus (item, status) { try { await api.updateUpstreamConfigRequest(item.id, { status }); await this.loadConfigRequests(); notify('申请状态已更新', 'success') } catch (error) { notify(error.message, 'error') } },
    setFilter (provider) { this.filter = provider; this.pagination.page = 1; this.load() },
    changePage (page) { this.pagination.page = page; this.load() },
    openCreate () { this.editingId = null; this.oauthSession = null; this.form = defaults('openai'); this.showForm = true; this.$nextTick(() => document.querySelector('.account-editor')?.scrollIntoView({ behavior: 'smooth' })) },
    openEdit (account) { this.editingId = account.id; this.oauthSession = null; this.form = { ...defaults(account.provider), provider: account.provider, name: account.name, models: (account.models || []).join(', '), priority: account.priority, weight: account.weight, input_price_cny: account.input_price_cny, output_price_cny: account.output_price_cny, price_multiplier: account.price_multiplier, enabled: account.enabled, mode: 'manual' }; this.showForm = true; this.$nextTick(() => document.querySelector('.account-editor')?.scrollIntoView({ behavior: 'smooth' })) },
    closeForm () { this.showForm = false; this.editingId = null; this.oauthSession = null },
    async startOAuth () { if (!this.form.name) return notify('请先填写账号名称', 'error'); if (!this.form.compliance_confirmed) return notify('请勾选账号授权与合规确认', 'error'); this.busy = true; try { const data = await api.authorizeUpstreamSubscription(this.payload()); this.oauthSession = data; window.open(data.authorization_url, '_blank', 'noopener') } catch (error) { notify(error.message, 'error') } finally { this.busy = false } },
    async finishOAuth () { this.busy = true; try { const data = await api.exchangeUpstreamSubscription({ ...this.payload(), session_id: this.oauthSession.session_id, callback_value: this.form.callback_value }); notify(data.message || 'OAuth 授权完成', 'success'); this.closeForm(); await this.load() } catch (error) { notify(error.message, 'error') } finally { this.busy = false } },
    async saveManual () { if (!this.form.name) return notify('请先填写账号名称', 'error'); if (!this.form.compliance_confirmed) return notify('请勾选账号授权与合规确认', 'error'); this.busy = true; try { const data = await api.createUpstreamSubscription({ ...this.payload(), auth_type: 'imported_token', access_token: this.form.access_token, refresh_token: this.form.refresh_token, expires_at: this.form.expires_at, email: this.form.email }); notify(data.message || '订阅账号已保存', 'success'); this.closeForm(); await this.load() } catch (error) { notify(error.message, 'error') } finally { this.busy = false } },
    async saveEdit () { if (!this.form.name) return notify('请先填写账号名称', 'error'); this.busy = true; try { const body = { ...this.payload(), access_token: this.form.access_token, refresh_token: this.form.refresh_token }; const data = await api.updateUpstreamSubscription(this.editingId, body); notify(data.message || '订阅账号已更新', 'success'); this.closeForm(); await this.load() } catch (error) { notify(error.message, 'error') } finally { this.busy = false } },
    async testAccount (account) { this.actionId = account.id; try { const data = await api.testUpstreamSubscription(account.id); notify(`${account.name}：${data.message || '连接正常'}${data.model_count ? `，发现 ${data.model_count} 个模型` : ''}`, 'success'); await this.load() } catch (error) { notify(error.message, 'error'); await this.load() } finally { this.actionId = null } },
    async refreshAccount (account) { this.actionId = account.id; try { const data = await api.refreshUpstreamSubscription(account.id); notify(data.message || 'Token 已刷新', 'success'); await this.load() } catch (error) { notify(error.message, 'error'); await this.load() } finally { this.actionId = null } },
    async toggleAccount (account) { try { await api.updateUpstreamSubscription(account.id, { enabled: !account.enabled }); notify(account.enabled ? '账号已停用' : '账号已启用', 'success'); await this.load() } catch (error) { notify(error.message, 'error') } },
    async removeAccount (account) { if (!await askConfirm(`确定删除订阅账号“${account.name}”吗？加密凭据也会一并删除。`)) return; try { const data = await api.deleteUpstreamSubscription(account.id); notify(data.message || '账号已删除', 'success'); await this.load() } catch (error) { notify(error.message, 'error') } }
  }
}
</script>

<style scoped>
.subscription-page{display:grid;gap:22px}.primary-action,.secondary-action,.text-action,.mode-tabs button,.provider-filter button,.account-actions button{border:1px solid var(--line,#d9dde5);background:var(--panel,#fff);color:inherit;border-radius:10px;padding:10px 15px;cursor:pointer}.primary-action{background:#17191d;color:#fff;border-color:#17191d;font-weight:700}.secondary-action{background:transparent}.text-action{padding:7px 11px}.primary-action:disabled,.account-actions button:disabled{opacity:.55;cursor:wait}.gateway-metrics{margin:0}.account-editor{display:grid;gap:18px}.mode-tabs,.provider-filter{display:flex;gap:8px;flex-wrap:wrap}.mode-tabs button.active,.provider-filter button.active{background:#17191d;color:#fff}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.form-grid label{display:grid;gap:7px}.form-grid label>span{font-size:13px;color:var(--muted,#6c7280);font-weight:600}.form-grid label>small{color:var(--muted,#6c7280);font-size:12px;line-height:1.5}.form-grid input,.form-grid select,.form-grid textarea,.oauth-step textarea{width:100%;box-sizing:border-box;border:1px solid var(--line,#d9dde5);border-radius:10px;background:var(--panel,#fff);color:inherit;padding:11px 12px;font:inherit}.span-2{grid-column:1/-1}.check-line{display:flex!important;align-items:center;grid-template-columns:auto 1fr!important}.check-line input,.compliance-check input{width:16px}.token-grid{padding-top:5px}.credential-update{border:1px solid var(--line,#d9dde5);border-radius:12px;padding:12px 14px}.credential-update summary{cursor:pointer;font-weight:700;margin-bottom:12px}.compliance-check{display:flex;gap:10px;align-items:flex-start;padding:14px;border:1px solid #e5c772;background:#fff9e7;color:#5b4810;border-radius:10px}.oauth-step{display:grid;gap:10px;padding:16px;border-radius:12px;background:#f1f5ff;border:1px solid #cbd8ff}.oauth-step p{margin:0;color:#566078}.oauth-step a{font-weight:700;color:#315cc8}.editor-actions{display:flex;gap:10px;flex-wrap:wrap}.account-list{display:grid;gap:14px}.account-card{display:grid;gap:14px;border:1px solid var(--line,#d9dde5);border-radius:14px;padding:17px}.account-main{display:flex;align-items:center;gap:12px}.provider-mark{display:grid;place-items:center;width:42px;height:42px;border-radius:12px;font-weight:900;background:#e7f1ff;color:#1856a7}.provider-mark.claude{background:#fff0e7;color:#9b4f1f}.account-title{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.account-main p{margin:4px 0 0;color:var(--muted,#6c7280);font-size:13px}.status-chip{font-size:11px;border-radius:99px;padding:4px 8px;background:#e9f7ee;color:#247143}.status-chip.invalid{background:#ffe7e7;color:#a12626}.status-chip.cooldown{background:#fff3d4;color:#876211}.status-chip.disabled{background:#eceef2;color:#666}.account-models{display:flex;gap:7px;flex-wrap:wrap}.account-models span{font-size:12px;background:var(--soft,#f4f5f7);border-radius:7px;padding:5px 8px}.account-facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:0}.account-facts div{display:grid;gap:3px}.account-facts dt{font-size:11px;color:var(--muted,#6c7280)}.account-facts dd{margin:0;font-size:13px;font-weight:650}.account-error{margin:0;padding:9px 11px;background:#fff1f1;color:#8d2929;border-radius:8px;font-size:12px;word-break:break-word}.account-actions{display:flex;gap:8px;flex-wrap:wrap}.account-actions button{padding:7px 10px;font-size:12px}.account-actions .danger{color:#b32828;border-color:#e9b8b8}.endpoint-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.endpoint-grid>div{display:grid;gap:8px;padding:16px;border:1px solid var(--line,#d9dde5);border-radius:12px}.endpoint-grid code{display:block;word-break:break-all;background:#17191d;color:#eaf0ff;border-radius:7px;padding:8px 10px}.endpoint-grid p{margin:0;color:var(--muted,#6c7280);line-height:1.55}.safety-note{margin:14px 0 0;padding:11px 13px;border-radius:9px;background:#fff9e7;border:1px solid #e5c772;color:#5b4810;font-size:13px;line-height:1.55}@media(max-width:850px){.form-grid,.endpoint-grid{grid-template-columns:1fr}.span-2{grid-column:auto}.account-facts{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.account-facts{grid-template-columns:1fr}.page-head{align-items:flex-start}.page-head>.primary-action{width:100%}}
.config-requests{display:grid;gap:18px}.config-requests .panel-head p{margin:6px 0 0;color:var(--muted,#6c7280)}.request-compose{display:grid;grid-template-columns:1.4fr 1fr .8fr auto auto;gap:10px}.request-compose input{min-width:0;border:1px solid var(--line,#d9dde5);border-radius:10px;background:var(--panel,#fff);color:inherit;padding:11px 12px;font:inherit}.proxy-choice{display:flex;align-items:center;gap:7px;padding:0 5px;white-space:nowrap}.proxy-choice input{width:16px}.request-list{display:grid;gap:9px}.request-list article{display:grid;grid-template-columns:minmax(220px,1fr) minmax(120px,.45fr) auto auto auto;align-items:center;gap:12px;padding:13px;border:1px solid var(--line,#d9dde5);border-radius:11px}.request-list article>div:first-child{display:grid;gap:3px}.request-list small{color:var(--muted,#6c7280);word-break:break-all}.request-list code{word-break:break-all}.proxy-badge{font-size:12px;color:var(--muted,#6c7280);white-space:nowrap}.request-status{padding:5px 8px;border-radius:99px;background:#fff3d4;color:#876211;font-size:12px}.request-status.accepted{background:#e9f7ee;color:#247143}.request-status.rejected{background:#ffe7e7;color:#a12626}.request-actions{display:flex;gap:6px;flex-wrap:wrap}.request-actions button{border:1px solid var(--line,#d9dde5);border-radius:8px;background:transparent;color:inherit;padding:6px 9px;cursor:pointer}@media(max-width:1100px){.request-compose,.request-list article{grid-template-columns:1fr 1fr}}@media(max-width:620px){.request-compose,.request-list article{grid-template-columns:1fr}}
/* Account pool cards follow the compact visual language used by plan management. */
.account-list {
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:12px;
}
.account-card {
  position:relative;
  min-width:0;
  gap:0;
  overflow:hidden;
  padding:16px;
  border:1px solid #d5e2ec;
  border-radius:12px;
  background:#fff;
  box-shadow:0 8px 18px #48677c12;
  transition:transform .2s,box-shadow .2s,border-color .2s;
}
.account-card::before {
  content:"";
  position:absolute;
  inset:0 0 auto;
  height:4px;
  background:linear-gradient(90deg,#3d92a8,#4d67ab);
}
.account-card.claude::before {
  background:linear-gradient(90deg,#d16e61,#d19b56);
}
.account-card:hover {
  transform:translateY(-3px);
  border-color:#a8c8df;
  box-shadow:0 18px 34px #48677c1f;
}
.account-card.claude:hover { border-color:#e2b3a0; }
.account-card-top {
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:10px;
  padding-top:3px;
}
.account-main { min-width:0;gap:9px; }
.provider-mark {
  flex:0 0 34px;
  width:34px;
  height:34px;
  border-radius:9px;
  font-size:13px;
}
.account-identity { min-width:0; }
.account-id {
  display:block;
  overflow:hidden;
  color:#7892a2;
  font:11px var(--mono);
  letter-spacing:.1em;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.account-identity h3 {
  overflow:hidden;
  margin:5px 0 0;
  color:#22435e;
  font-size:15px;
  line-height:1.25;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.status-chip { flex:0 0 auto;font-size:11px;padding:4px 7px; }
.account-summary {
  min-height:34px;
  margin:10px 0 9px;
  overflow:hidden;
  color:#758b9a;
  font-size:12px;
  line-height:1.6;
  overflow-wrap:anywhere;
}
.account-models {
  min-height:44px;
  align-content:flex-start;
  gap:5px;
  padding:9px 0;
  border-top:1px solid #e3edf2;
  border-bottom:1px solid #e3edf2;
}
.account-models span { padding:4px 6px;font-size:11px; }
.account-facts {
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:6px;
  margin:10px 0 0;
}
.account-facts div {
  min-width:0;
  padding:7px;
  border-radius:8px;
  background:#f2f7f9;
}
.account-card.claude .account-facts div { background:#fff5ed; }
.account-facts dt { font:11px var(--mono); }
.account-facts dd {
  overflow:hidden;
  margin-top:4px;
  color:#2a506d;
  font:500 12px var(--mono);
  text-overflow:ellipsis;
  white-space:nowrap;
}
.account-quota {
  display:grid;
  gap:10px;
  margin-top:11px;
  padding:11px;
  border:1px solid #dce9ef;
  border-radius:10px;
  background:#f7fafb;
}
.account-quota-head,.quota-plan,.quota-window>div,.quota-reset {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:8px;
}
.account-quota-head>div { display:grid;gap:2px; }
.account-quota-head small {
  color:#8299a7;
  font:8px var(--mono);
  letter-spacing:.08em;
}
.account-quota-head strong { color:#294d65;font-size:12px; }
.account-quota-head button {
  padding:4px 7px;
  border:1px solid #c8dce7;
  border-radius:6px;
  background:#fff;
  color:#52758a;
  font-size:10px;
}
.account-quota-head button:disabled { opacity:.6;cursor:wait; }
.quota-loading,.quota-error,.quota-empty {
  color:#78909e;
  font-size:11px;
  line-height:1.5;
}
.quota-error { color:#a15b57; }
.quota-plan span { color:#496d82;font:10px var(--mono);text-transform:uppercase; }
.quota-plan em {
  padding:3px 7px;
  border-radius:99px;
  background:#e4f3e9;
  color:#3f7759;
  font:normal 10px var(--sans);
}
.quota-plan em.limited { background:#f7e5de;color:#9b594b; }
.quota-window-list { display:grid;gap:9px; }
.quota-window { display:grid;gap:5px; }
.quota-window span,.quota-window b { font-size:10px; }
.quota-window b { color:#315b72;font-family:var(--mono);font-weight:500; }
.quota-window i {
  display:block;
  height:5px;
  overflow:hidden;
  border-radius:99px;
  background:#dfe9ed;
}
.quota-window u {
  display:block;
  height:100%;
  border-radius:inherit;
  background:linear-gradient(90deg,#4c9eb0,#6478bd);
  text-decoration:none;
}
.quota-window small,.quota-reset small { color:#8297a4;font-size:9px; }
.quota-reset { padding-top:8px;border-top:1px solid #e1ebef; }
.quota-reset>span { display:grid;gap:3px;min-width:0; }
.quota-reset>span:last-child { text-align:right; }
.quota-reset b {
  overflow:hidden;
  color:#355a70;
  font:500 10px var(--mono);
  text-overflow:ellipsis;
  white-space:nowrap;
}
.quota-warning { margin:0;color:#8f723d;font-size:10px;line-height:1.5; }
.account-error { margin:9px 0 0;font-size:12px; }
.account-actions {
  align-items:center;
  gap:6px;
  margin-top:11px;
}
.account-actions .secondary-btn {
  flex:1 1 58px;
  min-height:31px;
  padding:0 8px;
  border-radius:7px;
  background:#fff;
  font-size:12px;
}
.account-actions .text-btn {
  min-height:31px;
  padding:0 5px;
  border:0;
  background:transparent;
  font-size:12px;
}
.provider-filter .secondary-btn {
  min-height:36px;
  padding:0 14px;
  border-radius:9px;
  font-size:11px;
}
.provider-filter .secondary-btn.active {
  background:#17191d;
  border-color:#17191d;
  color:#fff;
}
:global(html[data-theme="dark"]) .account-card {
  border-color:#304b5c;
  background:#172735;
  box-shadow:0 8px 22px #050b102f;
}
:global(html[data-theme="dark"]) .account-identity h3,
:global(html[data-theme="dark"]) .account-facts dd { color:#d6e4ec; }
:global(html[data-theme="dark"]) .account-summary,
:global(html[data-theme="dark"]) .account-id { color:#93aabb; }
:global(html[data-theme="dark"]) .account-models { border-color:#2d4657; }
:global(html[data-theme="dark"]) .account-models span,
:global(html[data-theme="dark"]) .account-facts div { background:#203442;color:#c5d6df; }
:global(html[data-theme="dark"]) .account-quota {
  border-color:#315064;
  background:#1b2e3b;
}
:global(html[data-theme="dark"]) .account-quota-head strong,
:global(html[data-theme="dark"]) .quota-window b,
:global(html[data-theme="dark"]) .quota-reset b { color:#cce0ea; }
:global(html[data-theme="dark"]) .account-quota-head button {
  border-color:#45677b;
  background:#233c4c;
  color:#c1d7e3;
}
:global(html[data-theme="dark"]) .quota-window i { background:#294554; }
:global(html[data-theme="dark"]) .quota-reset { border-color:#2d4959; }

@media(max-width:1240px){.account-list{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:1050px){.account-list{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:600px){.account-list{grid-template-columns:1fr}}
</style>
