<template>
  <section class="page subscription-page">
    <div class="page-head">
      <div>
        <div class="eyebrow">SHARED SUBSCRIPTION POOL</div>
        <h1>共享订阅账号池</h1>
        <p>{{ isAdmin ? '管理全部共享订阅账号与用户提交的账号 Token 分享。' : '可查看全部共享账号；你只能维护自己添加的账号，凭据与运行状态不会向其他用户公开。' }}</p>
      </div>
      <button class="primary-action" type="button" @click="openCreate">添加订阅账号</button>
    </div>

    <div v-if="isAdmin" class="metric-grid gateway-metrics">
      <div class="metric-card"><span>账号总数</span><strong>{{ summary.total || 0 }}</strong><small>全部分页与供应商</small></div>
      <div class="metric-card"><span>OpenAI</span><strong>{{ providerCount('openai') }}</strong><small>Responses / Codex</small></div>
      <div class="metric-card"><span>其他供应商</span><strong>{{ otherProviderCount }}</strong><small>Claude / Grok / Kimi / GLM / MiniMax</small></div>
      <div class="metric-card" :class="{ highlight: gatewayEnabled }"><span>网关状态</span><strong>{{ gatewayEnabled ? '已启用' : '已停用' }}</strong><small>凭据全程加密保存</small></div>
    </div>
    <div v-if="isAdmin" class="gateway-strip gateway-capacity" aria-label="订阅账号池实时容量">
      <span><small>正在执行</small><b>{{ gatewayMetrics.active_requests || 0 }}</b></span>
      <span><small>API 直连</small><b>{{ gatewayMetrics.api_active_requests || 0 }}</b></span>
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
        <button v-if="oauthSupported(form.provider)" :class="{ active: form.mode === 'oauth' }" @click="form.mode = 'oauth'">OAuth 授权</button>
        <button :class="{ active: form.mode === 'manual' }" @click="form.mode = 'manual'">导入 Token / API Key</button>
      </div>

      <div class="form-grid">
        <label><span>供应商</span><select v-model="form.provider" :disabled="!!editingId || !!oauthSession"><option v-for="item in providers" :key="item.id" :value="item.id">{{ item.label }}</option></select></label>
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
          <label class="span-2"><span>{{ form.provider === 'openai' ? 'Access Token / Codex auth.json' : 'Access Token / API Key' }}</span><textarea v-model.trim="form.access_token" rows="5" autocomplete="off" :placeholder="form.provider === 'openai' ? '可粘贴 Codex auth.json，或其中 tokens.access_token；不要粘贴浏览器 Session Token' : '粘贴该供应商的 OAuth Access Token 或 Coding Plan API Key'"></textarea><small>{{ form.provider === 'openai' ? '完整 auth.json 会自动提取 access_token、refresh_token 与账号 ID；' : '' }}凭据由服务端加密保存，并在入池前验证连通性。</small></label>
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

      <label v-if="!editingId" class="compliance-check"><input v-model="form.compliance_confirmed" type="checkbox" /><span>我确认已获得此账号所有者授权，并会遵守所选上游供应商的服务条款、地区限制和账号共享规则。</span></label>

      <div v-if="oauthSession && !editingId" class="oauth-step">
        <strong>授权链接已生成</strong>
        <p>在新窗口完成授权后，把浏览器最终回调地址或页面显示的完整授权码粘贴到下方。</p>
        <a :href="oauthSession.authorization_url" target="_blank" rel="noopener">打开 {{ providerLabel(form.provider) }} 授权页面 ↗</a>
        <textarea v-model.trim="form.callback_value" rows="3" placeholder="OpenAI/Grok：粘贴本地回调完整地址；Claude：粘贴 code#state"></textarea>
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
        <div class="provider-filter"><button :class="['secondary-btn', { active: filter === '' }]" @click="setFilter('')">全部</button><button v-for="item in providers" :key="item.id" :class="['secondary-btn', { active: filter === item.id }]" @click="setFilter(item.id)">{{ item.short }}</button></div>
      </div>
      <div v-if="error" class="data-error" role="alert"><strong>订阅账号加载失败</strong><span>{{ error }}</span><button class="secondary-btn" @click="load">重试</button></div>
      <div v-if="loading" class="empty">正在加载订阅账号…</div>
      <div v-else-if="!accounts.length" class="empty">暂无订阅账号。添加后，对应模型会自动进入 `/v1/models`。</div>
      <div v-else class="account-groups">
        <section v-for="group in accountGroups" :key="group.id" :class="['account-group', group.id]">
          <div class="account-group-head">
            <h3><i aria-hidden="true"></i>{{ group.title }}<span>{{ group.accounts.length }} 个</span></h3>
            <button v-if="group.accounts.length > 3" type="button" class="secondary-btn group-toggle" :aria-expanded="String(group.expanded)" :aria-controls="'subscription-group-' + group.id" @click="toggleGroup(group.id)">{{ group.expanded ? '收起' : '展开全部（' + group.accounts.length + '）' }}</button>
          </div>
          <div v-if="!group.accounts.length" class="group-empty">暂无{{ group.title }}</div>
          <div v-else :id="'subscription-group-' + group.id" class="account-list">
        <article v-for="account in group.visibleAccounts" :key="account.id" :class="['account-card', account.provider]">
          <div class="account-card-top">
            <div class="account-main">
              <div class="provider-mark" :class="account.provider">{{ providerMark(account.provider) }}</div>
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
          <div v-if="quotaVisible && account.provider === 'openai'" class="account-quota">
            <div class="account-quota-head"><div><small>CODEX SUBSCRIPTION</small><strong>订阅剩余量</strong></div><button v-if="isAdmin && account.can_manage" type="button" :disabled="quotaState(account).loading" @click="loadAccountQuota(account, true)">{{ quotaState(account).loading ? '查询中…' : '刷新' }}</button><span v-else class="quota-readonly">随页面查询 · 5 分钟缓存</span></div>
            <div v-if="quotaState(account).loading && !quotaState(account).quota" class="quota-loading">正在安全查询订阅窗口…</div>
            <div v-else-if="quotaState(account).error && !quotaState(account).quota" class="quota-error">{{ quotaState(account).error }}</div>
            <template v-else-if="quotaState(account).quota">
              <div class="quota-plan"><span>{{ quotaState(account).quota.plan_type || '订阅套餐' }}</span><em :class="quotaState(account).quota.limit_reached ? 'limited' : ''">{{ quotaState(account).quota.limit_reached ? '已到上限' : '可用' }}</em></div>
              <div class="quota-window-list">
                <div v-for="item in quotaWindows(quotaState(account).quota)" :key="item.label + item.limit_window_seconds" class="quota-window">
                  <div><span>{{ item.label }}</span><b>剩余 {{ quotaPercent(item.remaining_percent) }}%</b></div>
                  <i><u :style="{ width: quotaPercent(item.remaining_percent) + '%' }"></u></i>
                  <small>已用 {{ quotaPercent(item.used_percent) }}% · {{ item.reset_at ? displayTime(item.reset_at) + ' 重置' : '重置时间未知' }}</small>
                </div>
                <div v-if="!quotaWindows(quotaState(account).quota).length" class="quota-empty">上游暂未返回用量窗口</div>
              </div>
              <div class="quota-reset"><span><small>可用重置次数</small><span class="quota-reset-value"><b>{{ resetCreditCount(quotaState(account).quota) }}</b><button v-if="account.can_manage && hasResetCredit(quotaState(account).quota)" type="button" :disabled="quotaResetId === account.id || quotaState(account).loading" @click="resetAccountQuota(account)">{{ quotaResetId === account.id ? '重置中…' : '重置额度' }}</button></span></span><span><small>最近查询</small><b>{{ displayTime(quotaState(account).quota.fetched_at) }}</b></span></div>
              <p v-if="quotaState(account).quota.warning" class="quota-warning">{{ quotaState(account).quota.warning }}</p>
            </template>
          </div>
          <p v-if="account.can_manage && account.last_error" class="account-error">{{ account.last_error }}</p>
          <div v-if="account.can_manage" class="account-actions">
            <button class="secondary-btn" :disabled="actionId === account.id" @click="testAccount(account)">测试连接</button>
            <button v-if="account.has_refresh_token" class="secondary-btn" :disabled="actionId === account.id" @click="refreshAccount(account)">刷新令牌</button>
            <button v-if="isAdmin" class="secondary-btn pricing-action" @click="openPricing(account)"><span>模型定价</span><b v-if="pricingOverrideCount(account)">{{ pricingOverrideCount(account) }}</b></button>
            <button class="secondary-btn" @click="openEdit(account)">编辑账号</button>
            <button class="secondary-btn status-action" @click="toggleAccount(account)">{{ account.enabled ? '停用账号' : '启用账号' }}</button>
            <button class="text-btn danger" @click="removeAccount(account)">删除账号</button>
          </div>
        </article>
          </div>
        </section>
      </div>
    </section>

    <div v-if="pricingAccount" class="pricing-backdrop" @click.self="closePricing">
      <section class="pricing-dialog" role="dialog" aria-modal="true" aria-labelledby="model-pricing-title">
        <header class="pricing-dialog-head">
          <div><span class="eyebrow">MODEL PRICING</span><h2 id="model-pricing-title">按模型修改定价</h2><p>{{ pricingAccount.name }} · 未开启单独定价的模型继续使用账号统一价格。</p></div>
          <button class="pricing-close" type="button" aria-label="关闭" @click="closePricing">×</button>
        </header>
        <div class="pricing-defaults">
          <span><small>统一输入价</small><b>¥{{ numberText(pricingAccount.input_price_cny) }}</b></span>
          <span><small>统一输出价</small><b>¥{{ numberText(pricingAccount.output_price_cny) }}</b></span>
          <span><small>统一倍率</small><b>{{ numberText(pricingAccount.price_multiplier) }}×</b></span>
        </div>
        <div v-if="pricingRows.length" class="model-pricing-list">
          <article v-for="row in pricingRows" :key="row.model" :class="{ custom: row.custom }">
            <div class="model-pricing-name"><strong>{{ row.model }}</strong><label><input v-model="row.custom" type="checkbox" /><span>单独定价</span></label></div>
            <label><span>输入价（¥/百万 Token）</span><input v-model.number="row.input_price_cny" :disabled="!row.custom" type="number" min="0" max="1000000" step="0.01" /></label>
            <label><span>输出价（¥/百万 Token）</span><input v-model.number="row.output_price_cny" :disabled="!row.custom" type="number" min="0" max="1000000" step="0.01" /></label>
            <label><span>价格倍率</span><input v-model.number="row.price_multiplier" :disabled="!row.custom" type="number" min="0" max="1000" step="0.01" /></label>
          </article>
        </div>
        <div v-else class="empty">此账号没有可单独定价的模型 ID。</div>
        <footer class="pricing-dialog-actions"><button class="secondary-action" type="button" @click="closePricing">取消</button><button class="primary-action" type="button" :disabled="pricingBusy || !pricingRows.length" @click="savePricing">{{ pricingBusy ? '保存中…' : '保存模型定价' }}</button></footer>
      </section>
    </div>

    <section class="panel config-requests">
      <div class="panel-head"><div><span class="eyebrow">SHARE ACCOUNT TOKEN</span><h2>分享自己账号的 Token</h2><p>可分享 DeepSeek、Kimi、Claude、GLM、MiniMax 等账号的 Token 或 API Key。</p><div class="share-provider-tags"><span>DeepSeek</span><span>Kimi</span><span>Claude</span><span>GLM</span><span>MiniMax</span><span>其他兼容平台</span></div></div></div>
      <div class="request-compose">
        <input v-model.trim="requestForm.url" type="url" placeholder="官方 API 地址，如 https://api.deepseek.com/v1" />
        <input v-model.trim="requestForm.api_key" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="账号 Token / API Key（输入内容可见）" />
        <input v-model.trim="requestForm.model_id" placeholder="模型 ID，如 deepseek-chat" />
        <label class="proxy-choice"><input v-model="requestForm.use_proxy" type="checkbox" /><span>走服务器代理</span></label>
        <button class="primary-action" :disabled="requestBusy" @click="submitConfigRequest">{{ requestBusy ? '提交中…' : '提交分享' }}</button>
      </div>
      <p class="share-safety-note">请只分享你本人拥有或已获授权的账号凭据，不要提交登录密码、浏览器 Cookie 或无权共享的 Token。</p>
      <div v-if="requestLoading" class="empty">正在加载分享记录…</div>
      <div v-else-if="!configRequests.length" class="empty">暂无 Token 分享记录</div>
      <div v-else class="request-list">
        <article v-for="item in configRequests" :key="item.id">
          <div><strong>{{ item.model_id }}</strong><small v-if="isAdmin">用户 #{{ item.user_id }}</small><small>{{ item.base_url }}</small></div>
          <code>{{ isAdmin ? (item.api_key || item.api_key_mask) : item.api_key_mask }}</code><span class="proxy-badge">{{ item.use_proxy ? '走代理' : '直连' }} · use-proxy {{ item.use_proxy ? 'true' : 'false' }}</span><span :class="['request-status', String(item.status).toLowerCase()]">{{ requestStatus(item.status) }}</span>
          <div v-if="isAdmin" class="request-actions"><button @click="copyConfigRequest(item)">复制配置</button><button @click="setRequestStatus(item, 'ACCEPTED')">已处理</button><button @click="setRequestStatus(item, 'REJECTED')">拒绝</button></div>
        </article>
      </div>
      <div v-if="requestPagination.pages > 1" class="pagination request-pagination"><button class="secondary-btn" :disabled="requestLoading || requestPagination.page <= 1" @click="changeRequestPage(requestPagination.page - 1)">上一页</button><span>第 {{ requestPagination.page }} / {{ requestPagination.pages }} 页，共 {{ requestPagination.total }} 条</span><button class="secondary-btn" :disabled="requestLoading || requestPagination.page >= requestPagination.pages" @click="changeRequestPage(requestPagination.page + 1)">下一页</button></div>
    </section>

    <section class="panel endpoint-help">
      <div class="panel-head"><div><span class="eyebrow">COMPATIBLE ENDPOINTS</span><h2>调用入口</h2></div></div>
      <div class="endpoint-grid"><div><strong>OpenAI / Grok</strong><code>POST /v1/chat/completions</code><code>POST /v1/responses</code><p>两种格式接入 Responses 订阅池；Chat Completions 由后端自动转换。</p></div><div><strong>Claude Subscription</strong><code>POST /v1/chat/completions</code><code>POST /v1/messages</code><code>POST /v1/messages/count_tokens</code><p>Claude 模型可统一使用 Chat Completions，后端自动转换 Anthropic Messages。</p></div><div><strong>Kimi / GLM / MiniMax Coding</strong><code>POST /v1/chat/completions</code><p>使用官方 Coding Plan API Key，共享既有计费、并发排队、失败重试与会话粘滞。</p></div></div>
      <p class="safety-note">账号池按优先级和平滑权重轮询，失败、冷却或限流时自动切换下一账号；同时采用单账号限并发、RPM 上限、退避与健康账号粘连，不伪造浏览器或人为操作，也不能保证上游账号不会被限制。</p>
    </section>
  </section>
</template>

<script>
import { api } from '../api'
import { askConfirm, notify } from '../ui'
import GatewayActivity from '../components/GatewayActivity.vue'
import { subscriptionGroups, loadSubscriptionAccounts } from '../subscriptionGroups.mjs'

const PROVIDERS = [
  { id: 'openai', label: 'OpenAI / Codex', short: 'OpenAI', mark: 'O', oauth: true, models: 'gpt-5.4, gpt-5.4-mini' },
  { id: 'claude', label: 'Claude / Anthropic', short: 'Claude', mark: 'C', oauth: true, models: 'claude-sonnet-4-6, claude-opus-4-6' },
  { id: 'grok', label: 'xAI / Grok', short: 'Grok', mark: 'G', oauth: true, models: 'grok-4.6, grok-4.5, grok-4.3' },
  { id: 'kimi', label: 'Kimi Coding', short: 'Kimi', mark: 'K', oauth: false, models: 'kimi-for-coding, kimi-k2' },
  { id: 'zhipu', label: '智谱 GLM Coding', short: 'GLM', mark: 'Z', oauth: false, models: 'glm-5.3, glm-5.3-flash, glm-5.2' },
  { id: 'minimax', label: 'MiniMax Coding', short: 'MiniMax', mark: 'M', oauth: false, models: 'MiniMax-M3, MiniMax-M2.7, MiniMax-M2.5' }
]
const providerMeta = provider => PROVIDERS.find(item => item.id === provider) || PROVIDERS[0]
const defaults = provider => ({ mode: providerMeta(provider).oauth ? 'oauth' : 'manual', provider, name: '', models: providerMeta(provider).models, priority: 0, weight: 1, input_price_cny: 0, output_price_cny: 0, price_multiplier: 1, enabled: true, access_token: '', refresh_token: '', expires_at: '', email: '', callback_value: '', compliance_confirmed: false })

export default {
  name: 'UpstreamSubscriptions',
  components: { GatewayActivity },
  props: { user: { type: Object, default: null }, subscriptionContributionsEnabled: { type: Boolean, default: false } },
  data: () => ({ providers: PROVIDERS, accounts: [], expandedGroups: { available: false, disabled: false }, loadSequence: 0, summary: {}, gatewayMetrics: {}, quotaByAccount: {}, quotaResetId: null, error: '', loading: false, busy: false, actionId: null, filter: '', showForm: false, editingId: null, oauthSession: null, gatewayEnabled: false, quotaVisible: true, metricsTimer: null, configRequests: [], requestPagination: { page: 1, page_size: 5, pages: 1, total: 0 }, requestLoading: false, requestBusy: false, requestForm: { url: '', api_key: '', model_id: '', use_proxy: false }, form: defaults('openai'), pricingAccount: null, pricingRows: [], pricingBusy: false }),
  computed: {
    isAdmin () { return String(this.user?.role || '').toUpperCase() === 'ADMIN' },
    otherProviderCount () { return PROVIDERS.filter(item => item.id !== 'openai').reduce((sum, item) => sum + this.providerCount(item.id), 0) },
    accountGroups () { return subscriptionGroups(this.accounts, this.expandedGroups) },
    visibleAccounts () { return this.accountGroups.flatMap(group => group.visibleAccounts) }
  },
  watch: {
    'form.provider' (next, previous) { if (!this.editingId && !this.oauthSession && next !== previous) { const fresh = defaults(next); this.form.models = fresh.models; this.form.mode = fresh.mode } },
    visibleAccounts () { this.loadAccountQuotas() }
  },
  created () { this.load(); this.loadConfigRequests(); if (this.isAdmin) this.metricsTimer = window.setInterval(this.refreshGatewayMetrics, 5000) },
  beforeUnmount () { window.clearInterval(this.metricsTimer) },
  beforeDestroy () { window.clearInterval(this.metricsTimer) },
  methods: {
    providerLabel (provider) { return providerMeta(provider).label },
    providerMark (provider) { return providerMeta(provider).mark },
    oauthSupported (provider) { return providerMeta(provider).oauth },
    providerCount (provider) { return Number(this.summary[provider] || 0) },
    numberText (value) { const number = Number(value || 0); return Number.isFinite(number) ? number.toLocaleString('zh-CN', { maximumFractionDigits: 8 }) : '0' },
    pricingOverrideCount (account) { return Object.keys(account?.model_pricing || {}).length },
    openPricing (account) {
      const overrides = account.model_pricing || {}
      this.pricingAccount = account
      this.pricingRows = (account.models || []).filter(model => model !== '*').map(model => {
        const pricing = overrides[model]
        return {
          model,
          custom: !!pricing,
          input_price_cny: Number(pricing?.input_price_cny ?? account.input_price_cny ?? 0),
          output_price_cny: Number(pricing?.output_price_cny ?? account.output_price_cny ?? 0),
          price_multiplier: Number(pricing?.price_multiplier ?? account.price_multiplier ?? 1)
        }
      })
    },
    closePricing () { if (this.pricingBusy) return; this.pricingAccount = null; this.pricingRows = [] },
    async savePricing () {
      if (!this.pricingAccount) return
      const modelPricing = {}
      this.pricingRows.filter(row => row.custom).forEach(row => { modelPricing[row.model] = { input_price_cny: row.input_price_cny, output_price_cny: row.output_price_cny, price_multiplier: row.price_multiplier } })
      this.pricingBusy = true
      try {
        const data = await api.updateUpstreamSubscription(this.pricingAccount.id, { model_pricing: modelPricing })
        Object.assign(this.pricingAccount, data.account || { model_pricing: modelPricing })
        notify(data.message || '模型定价已更新', 'success')
        this.pricingAccount = null
        this.pricingRows = []
      } catch (error) { notify(error.message || '模型定价保存失败', 'error') } finally { this.pricingBusy = false }
    },
    quotaState (account) { return this.quotaByAccount[account.id] || { loading: false, quota: null, error: '' } },
    quotaWindows (quota) { return [quota?.short_window, quota?.long_window].filter(Boolean) },
    quotaPercent (value) { return Math.min(100, Math.max(0, Number(value || 0))).toFixed(1).replace(/\.0$/, '') },
    resetCreditCount (quota) { const value = quota?.reset_credits?.available_count; return value === null || value === undefined ? '未提供' : `${Number(value).toLocaleString('zh-CN')} 次` },
    hasResetCredit (quota) { return Number(quota?.reset_credits?.available_count || 0) > 0 },
    setQuotaState (accountId, value) { this.quotaByAccount = { ...this.quotaByAccount, [accountId]: value } },
    async loadAccountQuota (account, refresh = false, quiet = false) {
      if (!this.quotaVisible || account.provider !== 'openai') return
      const previous = this.quotaState(account)
      this.setQuotaState(account.id, { ...previous, loading: true, error: '' })
      try {
        const data = await api.upstreamSubscriptionQuota(account.id, refresh && this.isAdmin && account.can_manage)
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
    loadAccountQuotas (refresh = false) {
      if (!this.quotaVisible) return
      this.visibleAccounts.filter(account => account.provider === 'openai').forEach(account => {
        const state = this.quotaState(account)
        if (!state.loading && (refresh || !state.quota)) this.loadAccountQuota(account, refresh && this.isAdmin && !!account.can_manage, true)
      })
    },
    async resetAccountQuota (account) {
      if (!account?.can_manage || !this.hasResetCredit(this.quotaState(account).quota) || this.quotaResetId !== null) return
      const count = this.resetCreditCount(this.quotaState(account).quota)
      if (!await askConfirm(`确定消耗 1 次重置次数，立即重置“${account.name}”的订阅额度吗？当前可用 ${count}，操作不可撤销。`)) return
      this.quotaResetId = account.id
      try {
        const data = await api.resetUpstreamSubscriptionQuota(account.id)
        if (data.quota) {
          this.setQuotaState(account.id, { loading: false, quota: data.quota, error: '' })
        } else {
          this.setQuotaState(account.id, { loading: false, quota: null, error: data.warning || '额度已提交重置，请稍后刷新确认，不要重复点击' })
        }
        const windows = Number(data.windows_reset || 0)
        notify(windows > 0 ? `订阅额度已重置，共恢复 ${windows} 个用量窗口` : '重置请求已成功提交，订阅余量已更新', 'success')
        if (data.warning) notify(data.warning, 'error')
      } catch (error) {
        notify(error.message || '订阅额度重置失败', 'error')
      } finally { this.quotaResetId = null }
    },
    statusClass (account) { return account.enabled ? String(account.status || 'READY').toLowerCase() : 'disabled' },
    statusText (account) { if (!account.enabled) return '已停用'; return ({ READY: '可用', INVALID: '凭据失效', COOLDOWN: '冷却中', DISABLED: '已停用' })[account.status] || account.status },
    displayTime (value) { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN') },
    payload () { return { provider: this.form.provider, name: this.form.name, models: this.form.models, priority: this.form.priority, weight: this.form.weight, input_price_cny: this.form.input_price_cny, output_price_cny: this.form.output_price_cny, price_multiplier: this.form.price_multiplier, enabled: this.form.enabled, compliance_confirmed: this.form.compliance_confirmed } },
    async load () {
      const sequence = ++this.loadSequence
      this.loading = true
      this.error = ''
      try {
        const data = await loadSubscriptionAccounts(params => api.upstreamSubscriptions(params), this.filter)
        if (sequence !== this.loadSequence) return
        this.accounts = data.accounts
        this.summary = data.summary || {}
        this.gatewayMetrics = data.gateway_metrics || {}
        this.gatewayEnabled = !!data.gateway_enabled
        this.quotaVisible = data.quota_visible !== false
        if (!this.quotaVisible) this.quotaByAccount = {}
        this.loadAccountQuotas(true)
      } catch (error) {
        if (sequence !== this.loadSequence) return
        this.error = error.message || '请检查后端服务后重试'
        notify(this.error, 'error')
      } finally { if (sequence === this.loadSequence) this.loading = false }
    },
    async refreshGatewayMetrics () { if (!this.isAdmin) return; try { const data = await api.upstreamGatewayMetrics(); this.gatewayMetrics = data.gateway_metrics || this.gatewayMetrics } catch (error) {} },
    requestStatus (status) { return ({ PENDING: '待处理', ACCEPTED: '已处理', REJECTED: '已拒绝' })[status] || status },
    async loadConfigRequests (page = this.requestPagination.page) { this.requestLoading = true; try { const requestedPage = Math.max(1, Number(page || 1)); const data = await api.upstreamConfigRequests({ page: requestedPage, page_size: this.requestPagination.page_size }); const rows = data.requests || []; this.requestPagination = data.pagination || { ...this.requestPagination, page: requestedPage }; this.configRequests = this.isAdmin ? await Promise.all(rows.map(async item => { try { const secret = await api.revealUpstreamConfigRequest(item.id); return { ...item, api_key: secret.request.api_key } } catch (error) { return item } })) : rows } catch (error) { notify(error.message || 'Token 分享记录加载失败', 'error') } finally { this.requestLoading = false } },
    async submitConfigRequest () { if (!this.requestForm.url || !this.requestForm.api_key || !this.requestForm.model_id) return notify('请完整填写官方 API 地址、账号 Token/API Key 和模型 ID', 'error'); this.requestBusy = true; try { await api.createUpstreamConfigRequest({ ...this.requestForm, token: this.requestForm.api_key }); notify('账号 Token 已加密提交给管理员', 'success'); this.requestForm = { url: '', api_key: '', model_id: '', use_proxy: false }; await this.loadConfigRequests(1) } catch (error) { notify(error.message, 'error') } finally { this.requestBusy = false } },
    async copyText (value) { if (navigator.clipboard?.writeText) { try { await navigator.clipboard.writeText(value); return } catch (error) {} } const textarea = document.createElement('textarea'); textarea.value = value; textarea.setAttribute('readonly', ''); textarea.style.position = 'fixed'; textarea.style.opacity = '0'; document.body.appendChild(textarea); textarea.select(); const copied = document.execCommand('copy'); document.body.removeChild(textarea); if (!copied) throw new Error('浏览器拒绝复制，请手动选择配置内容') },
    async copyConfigRequest (item) { try { const request = item.api_key ? { url: item.base_url, api_key: item.api_key, model_id: item.model_id, use_proxy: !!item.use_proxy } : (await api.revealUpstreamConfigRequest(item.id)).request; const value = `- id: ${request.model_id}\n  enabled: true\n  provider: OpenAI\n  endpoint: Chat\n  upstream-model: ${request.model_id}\n  base-url: ${request.url}\n  api-key: ${request.api_key}\n  use-proxy: ${request.use_proxy ? 'true' : 'false'}`; await this.copyText(value); notify('可粘贴到 rose.models 的 YAML 配置已复制', 'success') } catch (error) { notify(error.message || '复制失败', 'error') } },
    async setRequestStatus (item, status) { try { await api.updateUpstreamConfigRequest(item.id, { status }); await this.loadConfigRequests(); notify('Token 分享状态已更新', 'success') } catch (error) { notify(error.message, 'error') } },
    changeRequestPage (page) { this.loadConfigRequests(page) },
    setFilter (provider) { this.filter = provider; this.expandedGroups = { available: false, disabled: false }; this.load() },
    toggleGroup (groupId) { this.expandedGroups = { ...this.expandedGroups, [groupId]: !this.expandedGroups[groupId] } },
    openCreate () { this.editingId = null; this.oauthSession = null; this.form = defaults('openai'); this.showForm = true; this.$nextTick(() => document.querySelector('.account-editor')?.scrollIntoView({ behavior: 'smooth' })) },
    openEdit (account) { this.editingId = account.id; this.oauthSession = null; this.form = { ...defaults(account.provider), provider: account.provider, name: account.name, models: (account.models || []).join(', '), priority: account.priority, weight: account.weight, input_price_cny: account.input_price_cny, output_price_cny: account.output_price_cny, price_multiplier: account.price_multiplier, enabled: account.enabled, mode: 'manual' }; this.showForm = true; this.$nextTick(() => document.querySelector('.account-editor')?.scrollIntoView({ behavior: 'smooth' })) },
    closeForm () { this.showForm = false; this.editingId = null; this.oauthSession = null },
    async startOAuth () { if (!this.form.name) return notify('请先填写账号名称', 'error'); if (!this.form.compliance_confirmed) return notify('请勾选账号授权与合规确认', 'error'); this.busy = true; try { const data = await api.authorizeUpstreamSubscription(this.payload()); this.oauthSession = data; window.open(data.authorization_url, '_blank', 'noopener') } catch (error) { notify(error.message, 'error') } finally { this.busy = false } },
    async finishOAuth () { this.busy = true; try { const data = await api.exchangeUpstreamSubscription({ ...this.payload(), session_id: this.oauthSession.session_id, callback_value: this.form.callback_value }); notify(data.message || 'OAuth 授权完成', 'success'); this.closeForm(); await this.load() } catch (error) { notify(error.message, 'error') } finally { this.busy = false } },
    async saveManual () { if (!this.form.name) return notify('请先填写账号名称', 'error'); if (!this.form.compliance_confirmed) return notify('请勾选账号授权与合规确认', 'error'); this.busy = true; try { const data = await api.createUpstreamSubscription({ ...this.payload(), auth_type: this.oauthSupported(this.form.provider) ? 'imported_token' : 'api_key', access_token: this.form.access_token, refresh_token: this.form.refresh_token, expires_at: this.form.expires_at, email: this.form.email }); notify(data.message || '订阅账号已保存', 'success'); this.closeForm(); await this.load() } catch (error) { notify(error.message, 'error') } finally { this.busy = false } },
    async saveEdit () { if (!this.form.name) return notify('请先填写账号名称', 'error'); this.busy = true; try { const body = { ...this.payload(), access_token: this.form.access_token, refresh_token: this.form.refresh_token }; const data = await api.updateUpstreamSubscription(this.editingId, body); notify(data.message || '订阅账号已更新', 'success'); this.closeForm(); await this.load() } catch (error) { notify(error.message, 'error') } finally { this.busy = false } },
    async testAccount (account) { this.actionId = account.id; try { const data = await api.testUpstreamSubscription(account.id); notify(`${account.name}：${data.message || '连接正常'}${data.model_count ? `，发现 ${data.model_count} 个模型` : ''}`, 'success'); await this.load() } catch (error) { notify(error.message, 'error'); await this.load() } finally { this.actionId = null } },
    async refreshAccount (account) { this.actionId = account.id; try { const data = await api.refreshUpstreamSubscription(account.id); notify(data.message || 'Token 已刷新', 'success'); await this.load() } catch (error) { notify(error.message, 'error'); await this.load() } finally { this.actionId = null } },
    async toggleAccount (account) { try { await api.updateUpstreamSubscription(account.id, { enabled: !account.enabled }); notify(account.enabled ? '账号已停用' : '账号已启用', 'success'); await this.load() } catch (error) { notify(error.message, 'error') } },
    async removeAccount (account) { if (!await askConfirm(`确定删除订阅账号“${account.name}”吗？加密凭据也会一并删除。`)) return; try { const data = await api.deleteUpstreamSubscription(account.id); notify(data.message || '账号已删除', 'success'); await this.load() } catch (error) { notify(error.message, 'error') } }
  }
}
</script>

<style scoped>
.account-groups{display:grid;gap:26px}.account-group{min-width:0}.account-group-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.account-group-head h3{display:flex;align-items:center;gap:9px;margin:0;font-size:16px}.account-group-head h3 i{width:8px;height:8px;border-radius:50%;background:#519e83}.account-group.disabled .account-group-head h3 i{background:#86909c}.account-group-head h3 span{font-size:12px;font-weight:400;color:var(--muted,#6c7280)}.account-group.disabled{padding-top:22px;border-top:1px solid var(--line,#d9dde5)}.group-toggle{font-size:12px;white-space:nowrap}.group-empty{padding:18px 0;color:var(--muted,#6c7280);font-size:13px}
.subscription-page{display:grid;gap:22px}.primary-action,.secondary-action,.text-action,.mode-tabs button,.provider-filter button,.account-actions button{border:1px solid var(--line,#d9dde5);background:var(--panel,#fff);color:inherit;border-radius:10px;padding:10px 15px;cursor:pointer}.primary-action{background:#17191d;color:#fff;border-color:#17191d;font-weight:700}.secondary-action{background:transparent}.text-action{padding:7px 11px}.primary-action:disabled,.account-actions button:disabled{opacity:.55;cursor:wait}.gateway-metrics{margin:0}.account-editor{display:grid;gap:18px}.mode-tabs,.provider-filter{display:flex;gap:8px;flex-wrap:wrap}.mode-tabs button.active,.provider-filter button.active{background:#17191d;color:#fff}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.form-grid label{display:grid;gap:7px}.form-grid label>span{font-size:13px;color:var(--muted,#6c7280);font-weight:600}.form-grid label>small{color:var(--muted,#6c7280);font-size:12px;line-height:1.5}.form-grid input,.form-grid select,.form-grid textarea,.oauth-step textarea{width:100%;box-sizing:border-box;border:1px solid var(--line,#d9dde5);border-radius:10px;background:var(--panel,#fff);color:inherit;padding:11px 12px;font:inherit}.span-2{grid-column:1/-1}.check-line{display:flex!important;align-items:center;grid-template-columns:auto 1fr!important}.check-line input,.compliance-check input{width:16px}.token-grid{padding-top:5px}.credential-update{border:1px solid var(--line,#d9dde5);border-radius:12px;padding:12px 14px}.credential-update summary{cursor:pointer;font-weight:700;margin-bottom:12px}.compliance-check{display:flex;gap:10px;align-items:flex-start;padding:14px;border:1px solid #e5c772;background:#fff9e7;color:#5b4810;border-radius:10px}.oauth-step{display:grid;gap:10px;padding:16px;border-radius:12px;background:#f1f5ff;border:1px solid #cbd8ff}.oauth-step p{margin:0;color:#566078}.oauth-step a{font-weight:700;color:#315cc8}.editor-actions{display:flex;gap:10px;flex-wrap:wrap}.account-list{display:grid;gap:14px}.account-card{display:grid;gap:14px;border:1px solid var(--line,#d9dde5);border-radius:14px;padding:17px}.account-main{display:flex;align-items:center;gap:12px}.provider-mark{display:grid;place-items:center;width:42px;height:42px;border-radius:12px;font-weight:900;background:#e7f1ff;color:#1856a7}.provider-mark.claude{background:#fff0e7;color:#9b4f1f}.account-title{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.account-main p{margin:4px 0 0;color:var(--muted,#6c7280);font-size:13px}.status-chip{font-size:11px;border-radius:99px;padding:4px 8px;background:#e9f7ee;color:#247143}.status-chip.invalid{background:#ffe7e7;color:#a12626}.status-chip.cooldown{background:#fff3d4;color:#876211}.status-chip.disabled{background:#eceef2;color:#666}.account-models{display:flex;gap:7px;flex-wrap:wrap}.account-models span{font-size:12px;background:var(--soft,#f4f5f7);border-radius:7px;padding:5px 8px}.account-facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:0}.account-facts div{display:grid;gap:3px}.account-facts dt{font-size:11px;color:var(--muted,#6c7280)}.account-facts dd{margin:0;font-size:13px;font-weight:650}.account-error{margin:0;padding:9px 11px;background:#fff1f1;color:#8d2929;border-radius:8px;font-size:12px;word-break:break-word}.account-actions{display:flex;gap:8px;flex-wrap:wrap}.account-actions button{padding:7px 10px;font-size:12px}.account-actions .danger{color:#b32828;border-color:#e9b8b8}.endpoint-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.endpoint-grid>div{display:grid;gap:8px;padding:16px;border:1px solid var(--line,#d9dde5);border-radius:12px}.endpoint-grid code{display:block;word-break:break-all;background:#17191d;color:#eaf0ff;border-radius:7px;padding:8px 10px}.endpoint-grid p{margin:0;color:var(--muted,#6c7280);line-height:1.55}.safety-note{margin:14px 0 0;padding:11px 13px;border-radius:9px;background:#fff9e7;border:1px solid #e5c772;color:#5b4810;font-size:13px;line-height:1.55}@media(max-width:850px){.form-grid,.endpoint-grid{grid-template-columns:1fr}.span-2{grid-column:auto}.account-facts{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.account-facts{grid-template-columns:1fr}.page-head{align-items:flex-start}.page-head>.primary-action{width:100%}}
.config-requests{display:grid;gap:18px}.config-requests .panel-head p{margin:6px 0 0;color:var(--muted,#6c7280)}.share-provider-tags{display:flex;gap:7px;flex-wrap:wrap;margin-top:11px}.share-provider-tags span{padding:5px 9px;border:1px solid var(--line,#d9dde5);border-radius:99px;background:var(--soft,#f4f5f7);color:var(--muted,#6c7280);font-size:11px}.request-compose{display:grid;grid-template-columns:1.4fr 1fr .8fr auto auto;gap:10px}.request-compose input{min-width:0;border:1px solid var(--line,#d9dde5);border-radius:10px;background:var(--panel,#fff);color:inherit;padding:11px 12px;font:inherit}.proxy-choice{display:flex;align-items:center;gap:7px;padding:0 5px;white-space:nowrap}.proxy-choice input{width:16px}.share-safety-note{margin:-5px 0 0;padding:10px 12px;border:1px solid #e5c772;border-radius:9px;background:#fff9e7;color:#5b4810;font-size:12px;line-height:1.55}.request-list{display:grid;gap:9px}.request-list article{display:grid;grid-template-columns:minmax(220px,1fr) minmax(120px,.45fr) auto auto auto;align-items:center;gap:12px;padding:13px;border:1px solid var(--line,#d9dde5);border-radius:11px}.request-list article>div:first-child{display:grid;gap:3px}.request-list small{color:var(--muted,#6c7280);word-break:break-all}.request-list code{word-break:break-all}.proxy-badge{font-size:12px;color:var(--muted,#6c7280);white-space:nowrap}.request-status{padding:5px 8px;border-radius:99px;background:#fff3d4;color:#876211;font-size:12px}.request-status.accepted{background:#e9f7ee;color:#247143}.request-status.rejected{background:#ffe7e7;color:#a12626}.request-actions{display:flex;gap:6px;flex-wrap:wrap}.request-actions button{border:1px solid var(--line,#d9dde5);border-radius:8px;background:transparent;color:inherit;padding:6px 9px;cursor:pointer}.request-pagination{margin-top:0;padding-top:2px}:global(html[data-theme="dark"] .share-safety-note){border-color:#6a5928;background:#2a2413;color:#d8c783}@media(max-width:1100px){.request-compose,.request-list article{grid-template-columns:1fr 1fr}}@media(max-width:620px){.request-compose,.request-list article{grid-template-columns:1fr}}
.account-actions .pricing-action{border-color:#8eb7c8;background:#edf8fa;color:#32677c;font-weight:700}
.pricing-backdrop{position:fixed;z-index:1200;inset:0;display:grid;place-items:center;padding:24px;background:#0b1721a8;backdrop-filter:blur(5px)}
.pricing-dialog{display:grid;width:min(1040px,100%);max-height:min(820px,calc(100vh - 48px));overflow:auto;border:1px solid #cbdde7;border-radius:20px;background:#f8fbfc;box-shadow:0 30px 90px #07121d52}
.pricing-dialog-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;padding:24px 26px;border-bottom:1px solid #dbe7ed;background:linear-gradient(115deg,#eaf6f8,#f3f1fa)}
.pricing-dialog-head h2{margin:5px 0 0;color:#234e67}.pricing-dialog-head p{margin:7px 0 0;color:#718796;font-size:12px}.pricing-close{display:grid;place-items:center;width:38px;height:38px;border:1px solid #c9d9e2;border-radius:11px;background:#ffffffb8;color:#557184;font-size:24px;line-height:1;cursor:pointer}
.pricing-defaults{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;padding:16px 26px;border-bottom:1px solid #dce7ed}.pricing-defaults span{display:grid;gap:5px;padding:11px 13px;border:1px solid #d7e4ea;border-radius:11px;background:#fff}.pricing-defaults small{color:#7d929f;font-size:10px}.pricing-defaults b{color:#315b73;font:600 13px var(--mono)}
.model-pricing-list{display:grid;gap:9px;padding:18px 26px}.model-pricing-list article{display:grid;grid-template-columns:minmax(190px,1.25fr) repeat(3,minmax(150px,1fr));align-items:end;gap:10px;padding:14px;border:1px solid #d9e5eb;border-radius:13px;background:#fff}.model-pricing-list article.custom{border-color:#82b0c2;background:#f4fbfc;box-shadow:0 0 0 3px #5b9bb210}.model-pricing-name{display:grid;align-self:center;gap:8px;min-width:0}.model-pricing-name strong{overflow:hidden;color:#2c566e;font:600 12px var(--mono);text-overflow:ellipsis;white-space:nowrap}.model-pricing-name label{display:flex;align-items:center;gap:7px;color:#748b99;font-size:11px}.model-pricing-list article>label{display:grid;gap:6px;color:#6e8492;font-size:10px}.model-pricing-list input[type="number"]{width:100%;box-sizing:border-box;border:1px solid #cfdee6;border-radius:9px;background:#fff;color:#294f67;padding:9px 10px;font:500 12px var(--mono)}.model-pricing-list input:disabled{opacity:.58;background:#edf2f4}.pricing-dialog-actions{display:flex;justify-content:flex-end;gap:10px;padding:17px 26px;border-top:1px solid #dbe7ed;background:#f3f7f9}
:global(html[data-theme="dark"] .account-actions .pricing-action){border-color:#416b7e;background:#193845;color:#add1dd}
:global(html[data-theme="dark"] .pricing-backdrop){background:#02070bc2}
:global(html[data-theme="dark"] .pricing-dialog){border-color:#304d5e;background:#111e28;box-shadow:0 30px 90px #020609b8}
:global(html[data-theme="dark"] .pricing-dialog-head){border-color:#2c4656;background:linear-gradient(115deg,#172d38,#28283c)}
:global(html[data-theme="dark"] .pricing-dialog-head h2){color:#d0e1e9}
:global(html[data-theme="dark"] .pricing-dialog-head p){color:#8ea4b1}
:global(html[data-theme="dark"] .pricing-close){border-color:#385566;background:#1a2c38;color:#bdd0da}
:global(html[data-theme="dark"] .pricing-defaults){border-color:#2c4656}
:global(html[data-theme="dark"] .pricing-defaults span){border-color:#2e4a5b;background:#172934}
:global(html[data-theme="dark"] .pricing-defaults b){color:#c5dbe5}
:global(html[data-theme="dark"] .model-pricing-list article){border-color:#2d4859;background:#162732}
:global(html[data-theme="dark"] .model-pricing-list article.custom){border-color:#4d7d90;background:#17333e}
:global(html[data-theme="dark"] .model-pricing-name strong){color:#c5dbe6}
:global(html[data-theme="dark"] .model-pricing-list input[type="number"]){border-color:#345365;background:#13232e;color:#dbe7ed}
:global(html[data-theme="dark"] .model-pricing-list input:disabled){background:#1c2c35;color:#8195a1}
:global(html[data-theme="dark"] .pricing-dialog-actions){border-color:#2c4656;background:#13212b}
@media(max-width:850px){.model-pricing-list article{grid-template-columns:1fr 1fr}.model-pricing-name{grid-column:1/-1}.pricing-defaults{grid-template-columns:1fr}.pricing-backdrop{padding:10px}.pricing-dialog{max-height:calc(100vh - 20px)}}
/* Account pool cards follow the compact visual language used by plan management. */
.account-list {
  /* Give subscription names, dates and usage values room to remain visible. */
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:14px;
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
  overflow-wrap:anywhere;
  color:#7892a2;
  font:11px var(--mono);
  letter-spacing:.1em;
  white-space:normal;
}
.account-identity h3 {
  overflow-wrap:anywhere;
  margin:5px 0 0;
  color:#22435e;
  font-size:15px;
  line-height:1.25;
  white-space:normal;
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
  overflow-wrap:anywhere;
  margin-top:4px;
  color:#2a506d;
  font:500 12px var(--mono);
  white-space:normal;
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
.quota-readonly { color:#78909e;font-size:9px;white-space:nowrap; }
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
.quota-reset-value { display:flex;align-items:center;gap:7px; }
.quota-reset-value button {
  padding:3px 7px;
  border:1px solid #d0b46a;
  border-radius:6px;
  background:#fff9e8;
  color:#795f1d;
  font-size:9px;
  cursor:pointer;
}
.quota-reset-value button:disabled { opacity:.6;cursor:wait; }
.quota-reset b {
  overflow-wrap:anywhere;
  color:#355a70;
  font:500 10px var(--mono);
  white-space:normal;
}
.quota-warning { margin:0;color:#8f723d;font-size:10px;line-height:1.5; }
.account-error { margin:9px 0 0;font-size:12px; }
.account-actions {
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:7px;
  margin-top:12px;
  padding-top:12px;
  border-top:1px solid #e3edf2;
}
.account-actions .secondary-btn {
  display:flex;
  align-items:center;
  justify-content:center;
  gap:6px;
  min-width:0;
  min-height:36px;
  padding:0 9px;
  border-color:#d5e3eb;
  border-radius:9px;
  background:#f8fbfc;
  color:#486a7f;
  font-size:11px;
  font-weight:600;
  white-space:nowrap;
  transition:border-color .2s,background .2s,color .2s,transform .2s;
}
.account-actions .secondary-btn:hover { transform:translateY(-1px);border-color:#9cbccd;background:#eef6f8;color:#285d75; }
.account-actions .pricing-action { border-color:#a7cbd7;background:#edf7f9;color:#28677d; }
.account-actions .pricing-action b { display:grid;place-items:center;min-width:18px;height:18px;padding:0 4px;border-radius:99px;background:#d5edf1;color:#28677d;font:9px var(--mono); }
.account-actions .status-action { border-color:#dddfcf;background:#fafaf3;color:#6d7050; }
.account-actions .text-btn {
  min-width:0;
  min-height:36px;
  padding:0 9px;
  border:1px solid #edcfcc;
  border-radius:9px;
  background:#fff8f7;
  color:#a14f4a;
  font-size:11px;
  font-weight:600;
  white-space:nowrap;
  transition:border-color .2s,background .2s,color .2s,transform .2s;
}
.account-actions .text-btn:hover { transform:translateY(-1px);border-color:#dda8a2;background:#fff0ee;color:#8e3732; }
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
:global(html[data-theme="dark"] .account-card) {
  border-color:#304b5c;
  background:#172735;
  box-shadow:0 8px 22px #050b102f;
}
:global(html[data-theme="dark"] .account-identity h3),
:global(html[data-theme="dark"] .account-facts dd) { color:#d6e4ec; }
:global(html[data-theme="dark"] .account-summary),
:global(html[data-theme="dark"] .account-id) { color:#93aabb; }
:global(html[data-theme="dark"] .account-models) { border-color:#2d4657; }
:global(html[data-theme="dark"] .account-models span),
:global(html[data-theme="dark"] .account-facts div) { background:#203442;color:#c5d6df; }
:global(html[data-theme="dark"] .account-quota) {
  border-color:#315064;
  background:#1b2e3b;
}
:global(html[data-theme="dark"] .account-quota-head strong),
:global(html[data-theme="dark"] .quota-window b),
:global(html[data-theme="dark"] .quota-reset b) { color:#cce0ea; }
:global(html[data-theme="dark"] .account-quota-head button) {
  border-color:#45677b;
  background:#233c4c;
  color:#c1d7e3;
}
:global(html[data-theme="dark"] .quota-readonly) { color:#91a9b7; }
:global(html[data-theme="dark"] .quota-window i) { background:#294554; }
:global(html[data-theme="dark"] .quota-reset) { border-color:#2d4959; }
:global(html[data-theme="dark"] .quota-reset-value button) { border-color:#6b5a2d;background:#302a19;color:#e2cd8b; }
:global(html[data-theme="dark"] .account-actions) { border-color:#2d4657; }
:global(html[data-theme="dark"] .account-actions .secondary-btn) { border-color:#3a5869;background:#1c3140;color:#b6cbd6; }
:global(html[data-theme="dark"] .account-actions .secondary-btn:hover) { border-color:#527a8d;background:#23404f;color:#d4e5ec; }
:global(html[data-theme="dark"] .account-actions .pricing-action) { border-color:#477485;background:#1b3a45;color:#acd2dc; }
:global(html[data-theme="dark"] .account-actions .pricing-action b) { background:#29515d;color:#c9e2e8; }
:global(html[data-theme="dark"] .account-actions .status-action) { border-color:#59604e;background:#30352c;color:#ced1b4; }
:global(html[data-theme="dark"] .account-actions .text-btn) { border-color:#68474a;background:#39292d;color:#d8a5a1; }
:global(html[data-theme="dark"] .account-actions .text-btn:hover) { border-color:#8a5a5b;background:#472f33;color:#efc0bc; }

.provider-mark.grok{background:#e9e9ed;color:#222}.provider-mark.kimi{background:#eeeaff;color:#5940a5}.provider-mark.zhipu{background:#e7f7f2;color:#16745a}.provider-mark.minimax{background:#fff0f2;color:#a3344a}
.endpoint-grid{grid-template-columns:repeat(3,minmax(0,1fr))}

@media(max-width:1240px){.account-list{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:1050px){.account-list{grid-template-columns:repeat(2,minmax(0,1fr))}.endpoint-grid{grid-template-columns:1fr 1fr}}
@media(max-width:850px){.account-list{grid-template-columns:1fr}}
@media(max-width:600px){.account-list,.endpoint-grid{grid-template-columns:1fr}.account-actions{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
