<template>
  <section class="page">
    <div class="page-head"><div><div class="eyebrow">BILLING / WALLET</div><h1>余额与充值</h1><p>充值后余额可直接用于已配置模型的 API 调用。</p></div></div>
    <div class="billing-layout"><div class="balance-hero"><span>可用余额</span><strong>¥{{ Number(balance).toFixed(4) }}</strong><div><span>充值到账后即时可用</span><span>支持兑换码充值</span></div></div>
      <div class="panel recharge-panel"><div class="panel-head"><div><span class="eyebrow">ADD FUNDS</span><h2>选择充值金额</h2></div><span class="soft-label">最低 ¥{{ minimum }}</span></div><div class="amount-grid"><button v-for="amount in amounts" :key="amount" :class="{ active: form.amount === amount }" @click="form.amount = amount">¥{{ amount }}</button></div><div class="payment-options"><button class="active" @click="form.provider = 'WECHAT_PERSONAL'"><span class="pay-icon wechat">W</span>微信支付</button></div><button class="primary-btn full" :disabled="loading || !wechatAvailable" @click="createOrder">{{ loading ? '创建订单…' : wechatAvailable ? '创建充值订单' : '监听失效，暂不可用' }}</button></div>
    </div>
    <div class="panel subscription-panel">
      <div class="panel-head"><div><span class="eyebrow">SUBSCRIPTION PLANS</span><h2>固定套餐</h2><p class="panel-note">订阅后每日额度优先于钱包使用，套餐到期自动停止。</p></div></div>
      <div class="entitlement-explainer"><b>免费额度是赠送权益</b><span>管理员设置的免费额度会最先使用：正数表示该项有上限，<code>0</code> 表示仅该项不限制，金额、每日 Token、滚动窗口 Token 三项全为 <code>0</code> 才是有效期内不限量免费。免费额度用完后依次使用套餐和钱包；都不可用时请求会被拒绝。</span></div>
      <div v-if="subscriptionPlans.length" class="subscription-grid">
        <article v-for="plan in subscriptionPlans" :key="plan.id" class="subscription-card">
          <div class="subscription-card-head"><div><span class="subscription-badge">DAILY ACCESS</span><h3>{{ plan.name }}</h3><p>{{ plan.description || '按日提供模型调用额度' }}</p></div><div class="subscription-price"><small>套餐价</small><strong>¥{{ Number(plan.price || 0).toFixed(2) }}</strong></div></div>
          <div class="subscription-stats"><span><small>有效期</small><b>{{ plan.duration_days }} 天</b></span><span><small>每日金额</small><b>{{ plan.daily_amount > 0 ? `¥${Number(plan.daily_amount).toFixed(2)}` : '不限' }}</b></span><span><small>每日 Token</small><b>{{ plan.daily_tokens > 0 ? Number(plan.daily_tokens).toLocaleString('zh-CN') : '不限' }}</b></span></div>
          <button class="primary-btn full subscription-action" :disabled="subscribing === plan.id" @click="subscribePlan(plan)">{{ subscribing === plan.id ? '订阅中…' : '订阅套餐' }}<span>→</span></button>
        </article>
      </div>
      <div v-else class="empty compact-empty">暂无可用套餐</div>
      <div v-if="subscriptionPlanPagination.pages > 1" class="pagination"><button class="secondary-btn" :disabled="subscriptionPlanPagination.page <= 1" @click="changePlanPage(subscriptionPlanPagination.page - 1)">上一页</button><span>第 {{ subscriptionPlanPagination.page }} / {{ subscriptionPlanPagination.pages }} 页，共 {{ subscriptionPlanPagination.total }} 条</span><button class="secondary-btn" :disabled="subscriptionPlanPagination.page >= subscriptionPlanPagination.pages" @click="changePlanPage(subscriptionPlanPagination.page + 1)">下一页</button></div>
      <div v-if="visibleSubscriptions.length || visibleQuotas.length" class="entitlement-list"><div class="entitlement-title">当前权益（免费额度 → 套餐 → 钱包）</div><div v-for="item in visibleSubscriptions" :key="`sub-${item.id}`" class="entitlement-row subscription-entitlement-row"><span><b>{{ item.plan_name }}</b><small>有效至 {{ format(item.ends_at) }} · {{ item.auto_renew ? '自动续订已开启' : '默认不自动续订' }}</small></span><em>每日 {{ item.daily_tokens > 0 ? Number(item.daily_tokens).toLocaleString('zh-CN') + ' Token' : '不限 Token' }}{{ item.daily_amount > 0 ? ` · ¥${Number(item.daily_amount).toFixed(2)}` : '' }}</em><div class="subscription-entitlement-actions"><button class="text-btn" :disabled="renewing === item.id" @click="renewSubscription(item)">{{ renewing === item.id ? '续订中…' : '续订' }}</button><button class="text-btn" :disabled="autoRenewing === item.id" @click="toggleAutoRenew(item)">{{ autoRenewing === item.id ? '处理中…' : item.auto_renew ? '关闭自动续订' : '开启自动续订' }}</button></div></div><div v-for="item in visibleQuotas" :key="`quota-${item.id}`" class="entitlement-row"><span><b>{{ item.name }}（免费赠送）</b><small>{{ format(item.starts_at) }} 至 {{ item.ends_at ? format(item.ends_at) : '长期' }}</small></span><em>{{ quotaLimits(item) }}</em></div></div>
    </div>
    <div class="panel code-redeem"><div class="panel-head"><div><span class="eyebrow">REDEEM</span><h2>兑换码充值</h2></div></div><div class="custom-amount"><input v-model.trim="code" :placeholder="codePlaceholder" /><button class="secondary-btn" @click="redeem">兑换</button></div></div>
    <div v-if="paymentModalOpen && order" class="modal-backdrop"><div class="modal-card payment-modal"><div class="payment-modal-head"><div><span class="eyebrow">WECHAT PAYMENT</span><h2>微信支付</h2></div><span class="status pending">等待支付</span></div><p class="payment-order">订单号：<code>{{ order.trade_no }}</code></p><div class="payment-amount">¥{{ checkout.payment_amount || order.payment_amount }}</div><p class="payment-instruction">请使用微信扫描二维码，支付上方准确金额。支付成功后窗口会自动关闭，钱包和订单状态会自动刷新。</p><div class="payment-qrcode large"><img v-if="checkout.qr_image" :src="checkout.qr_image" alt="微信支付二维码" /></div><p class="muted center">遇到问题联系 QQ 1516933915 / 微信 17739798184</p><button class="secondary-btn full" :disabled="cancelling" @click="cancelCurrentOrder">{{ cancelling ? '取消中…' : '取消订单并关闭' }}</button></div></div>
    <div class="panel orders-panel"><div class="panel-head"><div><span class="eyebrow">HISTORY</span><h2>充值记录</h2></div><button class="icon-btn" @click="loadOrders">↻</button></div><table v-if="orders.length"><thead><tr><th>订单号</th><th>渠道</th><th>金额</th><th>状态</th><th>时间</th></tr></thead><tbody><tr v-for="item in orders" :key="item.id"><td><code>{{ item.trade_no }}</code></td><td>{{ labels[item.provider] || item.provider }}</td><td>¥{{ item.amount }}</td><td><span :class="['status', item.status === 'PAID' ? 'success' : item.status === 'CANCELLED' ? 'cancelled' : 'pending']">{{ item.status === 'PAID' ? '已支付' : item.status === 'CANCELLED' ? '已取消' : '待支付' }}</span></td><td>{{ format(item.created_at) }}</td></tr></tbody></table><div v-else class="empty compact-empty">暂无充值订单</div><div class="pagination" v-if="orderPagination.pages > 1"><button class="secondary-btn" :disabled="orderPagination.page <= 1" @click="changeOrderPage(orderPagination.page - 1)">上一页</button><span>第 {{ orderPagination.page }} / {{ orderPagination.pages }} 页，共 {{ orderPagination.total }} 条</span><button class="secondary-btn" :disabled="orderPagination.page >= orderPagination.pages" @click="changeOrderPage(orderPagination.page + 1)">下一页</button></div></div>
  </section>
</template>
<script>
import { api } from '../api'
import { notify, askConfirm } from '../ui'

export default {
  props: { rechargeCodePlaceholder: { type: String, default: '' } },
  data: () => ({
    plans: {}, subscriptionPlans: [], subscriptionPlanPagination: { page: 1, pages: 1, total: 0 }, subscriptions: [], quotas: [], entitlementNow: Date.now(), entitlementTimer: null, subscribing: 0, orders: [], orderPagination: { page: 1, pages: 1, total: 0 }, order: null, checkout: {}, wechatAvailable: false, wechatReason: '', listenerTimer: null,
    amounts: [10, 30, 100, 300, 1000],
    form: { amount: 10, provider: 'WECHAT_PERSONAL' },
    loading: false, cancelling: false, balance: 0, code: '', pollTimer: null, pollBusy: false, paymentModalOpen: false, renewing: 0, autoRenewing: 0,
    labels: { WECHAT_PERSONAL: '微信支付', WECHAT: '微信支付', ALIPAY: '支付宝', CODE: '兑换码' }
  }),
  computed: {
    minimum () { return Number(this.plans.min_recharge || 10) },
    providerLabel () { return this.labels[this.order?.provider] || '支付' },
    codePlaceholder () { return this.rechargeCodePlaceholder || '输入兑换码' },
    visibleSubscriptions () {
      const now = this.entitlementNow
      return this.subscriptions.filter(item => item.status === 'ACTIVE' && item.ends_at && new Date(item.ends_at).getTime() > now)
    },
    visibleQuotas () {
      const now = this.entitlementNow
      return this.quotas.filter(item => Number(item.enabled) === 1 && (!item.starts_at || new Date(item.starts_at).getTime() <= now) && (!item.ends_at || new Date(item.ends_at).getTime() > now))
    }
  },
  created () { this.load(); this.startListenerPolling(); this.entitlementTimer = window.setInterval(() => { this.entitlementNow = Date.now() }, 5000); document.addEventListener('visibilitychange', this.handleVisibilityChange) },
  beforeDestroy () { this.stopOrderPolling(); this.stopListenerPolling(); if (this.entitlementTimer) window.clearInterval(this.entitlementTimer); document.removeEventListener('visibilitychange', this.handleVisibilityChange) },
  methods: {
    startListenerPolling () { this.stopListenerPolling(); if (document.hidden) return; this.loadListenerStatus(); this.listenerTimer = window.setInterval(() => this.loadListenerStatus(), 15000) },
    stopListenerPolling () { if (this.listenerTimer) window.clearInterval(this.listenerTimer); this.listenerTimer = null },
    handleVisibilityChange () { if (document.hidden) { this.stopListenerPolling() } else { this.startListenerPolling(); if (this.order && this.order.status !== 'PAID' && this.order.status !== 'CANCELLED') this.refreshWalletAndOrder() } },
    async load () {
      try {
        const [p, o, d, s, q] = await Promise.all([api.plans({ page: this.subscriptionPlanPagination.page, page_size: 5 }), api.orders({ page: this.orderPagination.page, page_size: 5 }), api.dashboard({ page: 1, page_size: 5 }), api.subscriptions({ page: 1, page_size: 5 }), api.quotas({ page: 1, page_size: 5 })])
        this.plans = p; this.orders = o.orders || []; this.orderPagination = o.pagination || this.orderPagination; this.balance = d.user?.balance || 0
        this.subscriptionPlans = p.subscription_plans || []; this.subscriptionPlanPagination = p.subscription_plans_pagination || this.subscriptionPlanPagination; this.subscriptions = s.subscriptions || []; this.quotas = q.quotas || []
        this.syncCurrentOrder(this.orders)
      } catch (e) {}
    },
    async loadListenerStatus () {
      try {
        const d = await api.personalWechatStatus()
        const status = d.status || {}
        this.wechatAvailable = !!status.available
        this.wechatReason = status.reason ? `（${status.reason}）` : ''
      } catch (e) { this.wechatAvailable = false; this.wechatReason = '（无法读取监听状态）' }
    },
    syncCurrentOrder (orders) {
      if (!this.order || !Array.isArray(orders)) return
      const current = orders.find(item => item.trade_no === this.order.trade_no)
      if (current) {
        this.order = { ...this.order, ...current }
        if (current.status === 'PAID' || current.status === 'CANCELLED') {
          this.stopOrderPolling()
          this.paymentModalOpen = false
        }
      }
    },
    async refreshWalletAndOrder () {
      if (this.pollBusy || document.hidden) return
      this.pollBusy = true
      try {
        const [o, d] = await Promise.all([api.orders({ page: this.orderPagination.page, page_size: 5 }), api.dashboard({ page: 1, page_size: 5 })])
        this.orders = o.orders || []; this.orderPagination = o.pagination || this.orderPagination
        this.balance = d.user?.balance || this.balance
        this.syncCurrentOrder(this.orders)
      } finally { this.pollBusy = false }
    },
    startOrderPolling () {
      this.stopOrderPolling()
      if (!this.order || this.order.provider !== 'WECHAT_PERSONAL' || this.order.status === 'PAID') return
      this.pollTimer = window.setInterval(() => this.refreshWalletAndOrder(), 2000)
      this.refreshWalletAndOrder()
    },
    stopOrderPolling () {
      if (this.pollTimer) window.clearInterval(this.pollTimer)
      this.pollTimer = null
    },
    async loadOrders () {
      const d = await api.orders({ page: this.orderPagination.page, page_size: 5 }); this.orders = d.orders || []; this.orderPagination = d.pagination || this.orderPagination; this.syncCurrentOrder(this.orders)
    },
    changeOrderPage (page) { this.orderPagination.page = page; this.loadOrders() },
    changePlanPage (page) { this.subscriptionPlanPagination.page = page; this.load() },
    async subscribePlan (plan) {
      if (!await askConfirm(`确定订阅「${plan.name}」吗？将从钱包扣除 ¥${Number(plan.price || 0).toFixed(2)}。`)) return
      this.subscribing = plan.id
      try { const d = await api.subscribe({ plan_id: plan.id }); if (!d.ok) throw new Error(d.message); notify('套餐订阅成功', 'success'); await this.load() } catch (e) { notify(e.message, 'error') } finally { this.subscribing = 0 }
    },
    async cancelSubscription (item) {
      if (!await askConfirm(`确定取消「${item.plan_name}」吗？取消后不会再续期，当前有效期内额度仍可使用。`)) return
      try { const d = await api.cancelSubscription(item.id); if (!d.ok) throw new Error(d.message); notify(d.message || '自动续订已关闭', 'success'); await this.load() } catch (e) { notify(e.message, 'error') }
    },
    async renewSubscription (item) {
      if (this.renewing) return
      if (!await askConfirm(`确定续订「${item.plan_name}」吗？将从钱包扣除 ¥${Number(item.price || 0).toFixed(2)}，有效期顺延 ${item.duration_days || 30} 天。`)) return
      this.renewing = item.id
      try { const d = await api.renewSubscription(item.id); if (!d.ok) throw new Error(d.message); notify(d.message || '套餐已续订', 'success'); await this.load() } catch (e) { notify(e.message, 'error') } finally { this.renewing = 0 }
    },
    async toggleAutoRenew (item) {
      if (this.autoRenewing) return
      this.autoRenewing = item.id
      try { const d = await api.setSubscriptionAutoRenew(item.id, { enabled: !item.auto_renew }); if (!d.ok) throw new Error(d.message); notify(d.message || '自动续订设置已更新', 'success'); await this.load() } catch (e) { notify(e.message, 'error') } finally { this.autoRenewing = 0 }
    },
    async createOrder () {
      if (Number(this.form.amount) < this.minimum) return notify(`最低充值 ${this.minimum} 元`, 'error')
      this.stopOrderPolling(); this.loading = true
      try {
        const d = await api.createOrder(this.form)
        if (!d.ok) throw new Error(d.message)
        this.order = d.order; this.checkout = d.checkout || {}
        this.paymentModalOpen = this.order.provider === 'WECHAT_PERSONAL' && this.order.status === 'PENDING'
        await this.refreshWalletAndOrder()
        this.startOrderPolling()
      } catch (e) { notify(e.message, 'error') } finally { this.loading = false }
    },
    async cancelCurrentOrder () {
      if (!this.order || this.cancelling) return
      if (!await askConfirm('取消后该订单立即失效，之后收到相同金额也不会自动入账。确定取消吗？')) return
      this.cancelling = true
      try {
        const d = await api.cancelOrder(this.order.trade_no)
        if (!d.ok) throw new Error(d.message)
        this.order = d.order || { ...this.order, status: 'CANCELLED' }
        this.paymentModalOpen = false
        this.stopOrderPolling()
        await this.loadOrders()
      } catch (e) { notify(e.message, 'error') } finally { this.cancelling = false }
    },
    async redeem () {
      if (!this.code) return
      try {
        const d = await api.redeemCode({ code: this.code })
        if (!d.ok) throw new Error(d.message)
        const amount = Number(d.order?.amount || 0)
        notify(d.message || `兑换成功，已到账 ¥${amount.toFixed(2)}`, 'success'); this.code = ''; await this.load()
      } catch (e) { notify(e.message, 'error') }
    },
    quotaLimits (item) {
      const limits = []
      if (Number(item.daily_amount || 0) > 0) limits.push(`每日最多 ¥${Number(item.daily_amount).toFixed(4)}`)
      if (Number(item.daily_tokens || 0) > 0) limits.push(`每日最多 ${Number(item.daily_tokens).toLocaleString('zh-CN')} Token`)
      if (Number(item.hourly_tokens || 0) > 0) limits.push(`滚动 ${item.hourly_window_hours} 小时最多 ${Number(item.hourly_tokens).toLocaleString('zh-CN')} Token`)
      return limits.length ? limits.join(' · ') : '有效期内不限量免费'
    },
    format (s) { return s ? new Date(s).toLocaleString('zh-CN') : '-' }
  }
}
</script>


