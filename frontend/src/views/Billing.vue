<template>
  <section class="page">
    <div class="page-head"><div><div class="eyebrow">BILLING / WALLET</div><h1>余额与充值</h1><p>充值后余额可直接用于已配置模型的 API 调用。</p></div></div>
    <div v-if="loadError" class="data-error" role="alert"><strong>部分充值数据加载失败</strong><span>{{ loadError }}</span><button class="secondary-btn" @click="load">重试</button></div>
    <div class="billing-layout"><div class="balance-hero"><span>可用余额</span><strong>¥{{ Number(balance).toFixed(4) }}</strong><div><span>充值到账后即时可用</span><span>支持兑换码充值</span></div></div>
      <div class="panel recharge-panel"><div class="panel-head"><div><span class="eyebrow">ADD FUNDS</span><h2>选择充值金额</h2></div><span class="soft-label">最低 ¥{{ minimum }}</span></div><div class="amount-grid"><button v-for="amount in amounts" :key="amount" :class="{ active: form.amount === amount }" @click="form.amount = amount">¥{{ amount }}</button></div><div class="payment-options"><button class="active" @click="form.provider = 'WECHAT_PERSONAL'"><span class="pay-icon wechat">W</span>微信支付</button></div><button class="primary-btn full" :disabled="loading || !wechatAvailable" @click="createOrder">{{ loading ? '创建订单…' : wechatAvailable ? '创建充值订单' : '监听失效，暂不可用' }}</button><small v-if="!wechatAvailable" class="payment-unavailable">{{ wechatReason || '支付监听器当前不可用' }}</small></div>
    </div>
    <div class="panel code-redeem"><div class="panel-head"><div><span class="eyebrow">REDEEM</span><h2>兑换码充值</h2></div></div><div class="custom-amount"><input v-model.trim="code" :placeholder="codePlaceholder" /><button class="secondary-btn" @click="redeem">兑换</button></div></div>
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
    </div>
    <div class="panel entitlement-overview-panel">
      <div class="entitlement-list">
        <div class="entitlement-title"><span>套餐与免费赠送用量</span><small>每日统计按 UTC 00:00 重置；滚动窗口按标注小时实时计算</small></div>
        <section v-if="entitlements.length" class="entitlement-group entitlement-combined-list">
          <article v-for="item in entitlements" :key="`${item.entitlement_type}-${item.id}`" :class="['entitlement-row', { 'subscription-entitlement-row': item.entitlement_type === 'SUBSCRIPTION' }]">
            <template v-if="item.entitlement_type === 'FREE'">
              <div class="entitlement-main"><span><b>{{ item.name }}</b><i class="entitlement-kind gift">免费赠送</i></span><small>{{ format(item.starts_at) }} 至 {{ item.ends_at ? format(item.ends_at) : '长期' }}</small><em>{{ quotaLimits(item) }}</em></div>
              <div class="entitlement-usage-grid three-columns">
                <div class="entitlement-usage"><small>今日免费金额</small><b>已用 {{ usageValue(item, 'daily_amount', true) }}</b><span>剩余 {{ remainingValue(item, 'daily_amount', true) }}</span><i><u :style="{ width: usagePercent(item, 'daily_amount') + '%' }"></u></i></div>
                <div class="entitlement-usage"><small>今日免费 Token</small><b>已用 {{ usageValue(item, 'daily_tokens') }}</b><span>剩余 {{ remainingValue(item, 'daily_tokens') }}</span><i><u :style="{ width: usagePercent(item, 'daily_tokens') + '%' }"></u></i></div>
                <div class="entitlement-usage"><small>滚动 {{ usageWindow(item) }} 小时 Token</small><b>已用 {{ usageValue(item, 'window_tokens') }}</b><span>剩余 {{ remainingValue(item, 'window_tokens') }}</span><i><u :style="{ width: usagePercent(item, 'window_tokens') + '%' }"></u></i></div>
              </div>
              <span :class="['status', item.usage?.active ? 'success' : 'pending']">{{ item.usage?.active ? '可用' : '不可用' }}</span>
            </template>
            <template v-else>
              <div class="entitlement-main"><span><b>{{ item.plan_name }}</b><i class="entitlement-kind plan">付费套餐</i></span><small>有效至 {{ format(item.ends_at) }} · {{ item.auto_renew ? '自动续订已开启' : '默认不自动续订' }}</small><em>套餐价 ¥{{ Number(item.price || 0).toFixed(2) }}</em></div>
              <div class="entitlement-usage-grid">
                <div class="entitlement-usage"><small>今日套餐金额</small><b>已用 {{ usageValue(item, 'daily_amount', true) }}</b><span>剩余 {{ remainingValue(item, 'daily_amount', true) }}</span><i><u :style="{ width: usagePercent(item, 'daily_amount') + '%' }"></u></i></div>
                <div class="entitlement-usage"><small>今日套餐 Token</small><b>已用 {{ usageValue(item, 'daily_tokens') }}</b><span>剩余 {{ remainingValue(item, 'daily_tokens') }}</span><i><u :style="{ width: usagePercent(item, 'daily_tokens') + '%' }"></u></i></div>
              </div>
              <div class="subscription-entitlement-actions"><span :class="['status', item.usage?.active ? 'success' : 'pending']">{{ item.usage?.active ? '可用' : subscriptionStatus(item.status) }}</span><button v-if="item.usage?.active" class="text-btn" :disabled="renewing === item.id" @click="renewSubscription(item)">{{ renewing === item.id ? '续订中…' : '续订' }}</button><button v-if="item.usage?.active" class="text-btn" :disabled="autoRenewing === item.id" @click="toggleAutoRenew(item)">{{ autoRenewing === item.id ? '处理中…' : item.auto_renew ? '关闭自动续订' : '开启自动续订' }}</button></div>
            </template>
          </article>
        </section>
        <div v-if="!entitlements.length" class="empty compact-empty">暂无套餐或免费赠送记录</div>
        <div v-if="entitlementPagination.pages > 1" class="pagination entitlement-pagination"><button class="secondary-btn" :disabled="entitlementPagination.page <= 1" @click="changeEntitlementPage(entitlementPagination.page - 1)">上一页</button><span>第 {{ entitlementPagination.page }} / {{ entitlementPagination.pages }} 页，共 {{ entitlementPagination.total }} 条</span><button class="secondary-btn" :disabled="entitlementPagination.page >= entitlementPagination.pages" @click="changeEntitlementPage(entitlementPagination.page + 1)">下一页</button></div>
      </div>
    </div>
    <div v-if="paymentModalOpen && order" class="modal-backdrop"><div ref="paymentDialog" class="modal-card payment-modal" role="dialog" aria-modal="true" aria-labelledby="payment-title" tabindex="-1" @keydown="trapPaymentFocus"><div class="payment-modal-head"><div><span class="eyebrow">WECHAT PAYMENT</span><h2 id="payment-title">微信支付</h2></div><span class="status pending">等待支付</span></div><p class="payment-order">订单号：<code>{{ order.trade_no }}</code></p><div class="payment-amount">¥{{ checkout.payment_amount || order.payment_amount }}</div><p class="payment-instruction">请使用微信扫描二维码，支付上方准确金额。支付成功后窗口会自动关闭，钱包和订单状态会自动刷新。</p><div class="payment-qrcode large"><img v-if="checkout.qr_image" :src="checkout.qr_image" alt="微信支付二维码" /></div><p class="muted center">遇到问题联系 QQ 1516933915 / 微信 17739798184</p><button class="secondary-btn full" :disabled="cancelling" @click="cancelCurrentOrder">{{ cancelling ? '取消中…' : '取消订单并关闭' }}</button></div></div>
    <div class="panel orders-panel"><div class="panel-head"><div><span class="eyebrow">HISTORY</span><h2>充值记录</h2></div><button class="icon-btn" @click="loadOrders">↻</button></div><table v-if="orders.length"><thead><tr><th>订单号</th><th>渠道</th><th>金额</th><th>状态</th><th>时间</th></tr></thead><tbody><tr v-for="item in orders" :key="item.id"><td><code>{{ item.trade_no }}</code></td><td>{{ labels[item.provider] || item.provider }}</td><td>¥{{ item.amount }}</td><td><span :class="['status', item.status === 'PAID' ? 'success' : item.status === 'CANCELLED' ? 'cancelled' : 'pending']">{{ item.status === 'PAID' ? '已支付' : item.status === 'CANCELLED' ? '已取消' : '待支付' }}</span></td><td>{{ format(item.created_at) }}</td></tr></tbody></table><div v-else class="empty compact-empty">暂无充值订单</div><div class="pagination" v-if="orderPagination.pages > 1"><button class="secondary-btn" :disabled="orderPagination.page <= 1" @click="changeOrderPage(orderPagination.page - 1)">上一页</button><span>第 {{ orderPagination.page }} / {{ orderPagination.pages }} 页，共 {{ orderPagination.total }} 条</span><button class="secondary-btn" :disabled="orderPagination.page >= orderPagination.pages" @click="changeOrderPage(orderPagination.page + 1)">下一页</button></div></div>
  </section>
</template>
<script>
import { api } from '../api'
import { notify, askConfirm, focusDialog, trapDialogFocus } from '../ui'

export default {
  props: { rechargeCodePlaceholder: { type: String, default: '' } },
  data: () => ({
    plans: {}, subscriptionPlans: [], subscriptionPlanPagination: { page: 1, pages: 1, total: 0 }, entitlements: [], entitlementPagination: { page: 1, pages: 1, total: 0 }, subscribing: 0, orders: [], orderPagination: { page: 1, pages: 1, total: 0 }, order: null, checkout: {}, wechatAvailable: false, wechatReason: '', listenerTimer: null,
    amounts: [10, 30, 100, 300, 1000],
    form: { amount: 10, provider: 'WECHAT_PERSONAL' },
    loading: false, pageLoading: false, loadError: '', cancelling: false, balance: 0, code: '', pollTimer: null, pollBusy: false, paymentModalOpen: false, dialogReturnFocus: null, renewing: 0, autoRenewing: 0,
    labels: { WECHAT_PERSONAL: '微信支付', WECHAT: '微信支付', ALIPAY: '支付宝', CODE: '兑换码' }
  }),
  computed: {
    minimum () { return Number(this.plans.min_recharge || 10) },
    providerLabel () { return this.labels[this.order?.provider] || '支付' },
    codePlaceholder () { return this.rechargeCodePlaceholder || '输入兑换码' }
  },
  created () { this.load(); this.startListenerPolling(); document.addEventListener('visibilitychange', this.handleVisibilityChange) },
  beforeDestroy () { this.stopOrderPolling(); this.stopListenerPolling(); document.removeEventListener('visibilitychange', this.handleVisibilityChange) },
  methods: {
    startListenerPolling () { this.stopListenerPolling(); if (document.hidden) return; this.loadListenerStatus(); this.listenerTimer = window.setInterval(() => this.loadListenerStatus(), 15000) },
    stopListenerPolling () { if (this.listenerTimer) window.clearInterval(this.listenerTimer); this.listenerTimer = null },
    handleVisibilityChange () { if (document.hidden) { this.stopListenerPolling() } else { this.startListenerPolling(); if (this.order && this.order.status !== 'PAID' && this.order.status !== 'CANCELLED') this.refreshWalletAndOrder() } },
    async load () {
      this.pageLoading = true; this.loadError = ''
      const results = await Promise.allSettled([api.plans({ page: this.subscriptionPlanPagination.page, page_size: 5 }), api.orders({ page: this.orderPagination.page, page_size: 5 }), api.dashboard({ page: 1, page_size: 5 }), api.entitlements({ page: this.entitlementPagination.page, page_size: 5 })])
      const [planResult, orderResult, dashboardResult, entitlementResult] = results
      if (planResult.status === 'fulfilled') { const p = planResult.value; this.plans = p; this.subscriptionPlans = p.subscription_plans || []; this.subscriptionPlanPagination = p.subscription_plans_pagination || this.subscriptionPlanPagination }
      if (orderResult.status === 'fulfilled') { const o = orderResult.value; this.orders = o.orders || []; this.orderPagination = o.pagination || this.orderPagination; this.syncCurrentOrder(this.orders) }
      if (dashboardResult.status === 'fulfilled') this.balance = (dashboardResult.value.user && dashboardResult.value.user.balance) || 0
      if (entitlementResult.status === 'fulfilled') { const e = entitlementResult.value; this.entitlements = e.entitlements || []; this.entitlementPagination = e.pagination || this.entitlementPagination }
      const failed = results.filter(item => item.status === 'rejected')
      if (failed.length) this.loadError = `${failed.length} 个区块暂时不可用，其余数据已正常显示。`
      this.pageLoading = false
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
          this.restorePaymentFocus()
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
      try { const d = await api.orders({ page: this.orderPagination.page, page_size: 5 }); this.orders = d.orders || []; this.orderPagination = d.pagination || this.orderPagination; this.syncCurrentOrder(this.orders) } catch (e) { notify(e.message, 'error') }
    },
    changeOrderPage (page) { this.orderPagination.page = page; this.loadOrders() },
    changePlanPage (page) { this.subscriptionPlanPagination.page = page; this.load() },
    changeEntitlementPage (page) { this.entitlementPagination.page = page; this.load() },
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
      this.dialogReturnFocus = document.activeElement
      this.stopOrderPolling(); this.loading = true
      try {
        const d = await api.createOrder(this.form)
        if (!d.ok) throw new Error(d.message)
        this.order = d.order; this.checkout = d.checkout || {}
        this.paymentModalOpen = this.order.provider === 'WECHAT_PERSONAL' && this.order.status === 'PENDING'
        if (this.paymentModalOpen) this.$nextTick(() => focusDialog(this.$refs.paymentDialog))
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
        this.restorePaymentFocus()
        this.stopOrderPolling()
        await this.loadOrders()
      } catch (e) { notify(e.message, 'error') } finally { this.cancelling = false }
    },
    trapPaymentFocus (event) { trapDialogFocus(event, this.$refs.paymentDialog) },
    restorePaymentFocus () { this.$nextTick(() => this.dialogReturnFocus && this.dialogReturnFocus.focus && this.dialogReturnFocus.focus()) },
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
    subscriptionStatus (status) { return { ACTIVE: '暂不可用', DISABLED: '已停用', EXPIRED: '已过期', CANCELLED: '已取消' }[status] || status || '不可用' },
    usageDimension (item, key) { return item?.usage?.[key] || { limit: 0, used: 0, remaining: 0, unlimited: false } },
    usageValue (item, key, money = false) {
      const value = Number(this.usageDimension(item, key).used || 0)
      return money ? `¥${value.toFixed(4)}` : `${value.toLocaleString('zh-CN')} Token`
    },
    remainingValue (item, key, money = false) {
      const usage = this.usageDimension(item, key)
      if (!item?.usage?.active) return '0（不可用）'
      if (usage.unlimited) return '不限'
      const value = Number(usage.remaining || 0)
      return money ? `¥${value.toFixed(4)}` : `${value.toLocaleString('zh-CN')} Token`
    },
    usagePercent (item, key) {
      const usage = this.usageDimension(item, key)
      const limit = Number(usage.limit || 0)
      return limit > 0 ? Math.min(100, Math.max(0, Number(usage.used || 0) / limit * 100)) : 0
    },
    usageWindow (item) { return Number(item?.usage?.window_tokens?.window_hours || item.hourly_window_hours || 1).toLocaleString('zh-CN') },
    format (s) { return s ? new Date(s).toLocaleString('zh-CN') : '-' }
  }
}
</script>


