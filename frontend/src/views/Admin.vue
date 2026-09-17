<template>
  <section class="page">
    <div class="page-head">
      <div>
        <div class="eyebrow">ADMIN / OPERATIONS</div>
        <h1>管理后台</h1>
        <p>管理用户、余额、权限、兑换码和运行数据。</p>
      </div>
    </div>

    <nav class="admin-overview-tabs" aria-label="管理后台分区">
      <button :class="{ active: activeAdminSection === 'business' }" @click="activeAdminSection = 'business'">业务管理</button>
      <button :class="{ active: activeAdminSection === 'visuals' }" @click="activeAdminSection = 'visuals'">数据可视化</button>
      <button :class="{ active: activeAdminSection === 'logs' }" @click="activeAdminSection = 'logs'">后台日志</button>
      <button :class="{ active: activeAdminSection === 'device' }" @click="activeAdminSection = 'device'">设备信息</button>
    </nav>

    <nav v-if="activeAdminSection === 'business'" class="business-anchor-nav" aria-label="业务管理快速导航">
      <button :class="{ active: businessAnchor === 'plans' }" @click="scrollToBusinessSection('plans')"><span>01</span><strong>套餐管理</strong></button>
      <button :class="{ active: businessAnchor === 'codes' }" @click="scrollToBusinessSection('codes')"><span>02</span><strong>兑换码</strong></button>
      <button :class="{ active: businessAnchor === 'groups' }" @click="scrollToBusinessSection('groups')"><span>03</span><strong>用户分组</strong></button>
      <button :class="{ active: businessAnchor === 'users' }" @click="scrollToBusinessSection('users')"><span>04</span><strong>用户列表</strong></button>
    </nav>

    <div v-if="activeAdminSection === 'business'" id="admin-business-overview" class="metric-grid admin-metrics">
      <div class="metric-card"><span>用户总数</span><strong>{{ summary.users || 0 }}</strong><small>已注册账户</small></div>
      <div class="metric-card"><span>活跃用户</span><strong>{{ summary.active_users || 0 }}</strong><small>当前可调用</small></div>
      <div class="metric-card"><span>总请求</span><strong>{{ summary.total_requests || 0 }}</strong><small>全站请求</small></div>
      <div class="metric-card"><span>总消耗 Token</span><strong>{{ tokens(summary.total_tokens) }}</strong><small>全站累计消耗量</small></div>
      <div class="metric-card"><span>总消耗费用</span><strong>¥{{ Number(summary.total_cost || 0).toFixed(4) }}</strong><small>全站累计模型费用</small></div>
      <div class="metric-card highlight"><span>累计充值</span><strong>¥{{ Number(summary.total_recharge || 0).toFixed(2) }}</strong><small>已支付订单</small></div>
    </div>

    <div v-if="activeAdminSection === 'business'" class="metric-grid admin-runtime-metrics">
      <div class="metric-card runtime-card"><span>运行期吞吐</span><strong>{{ Number(requestMetrics.throughput_rpm || 0).toFixed(0) }}</strong><small>最近 60 秒请求数</small></div>
      <div class="metric-card runtime-card"><span>平均延迟</span><strong>{{ Number(requestMetrics.average_latency_ms || 0).toFixed(0) }}<em>ms</em></strong><small>运行期 HTTP 请求</small></div>
      <div class="metric-card runtime-card warning"><span>警告 / 异常</span><strong>{{ requestMetrics.warnings || 0 }} / {{ requestMetrics.exceptions || 0 }}</strong><small>后台运行期记录</small></div>
      <div class="metric-card runtime-card danger"><span>丢包率</span><strong>{{ Number(requestMetrics.packet_loss_rate || 0).toFixed(2) }}<em>%</em></strong><small>5xx 或未完成请求</small></div>
    </div>

    <section v-if="activeAdminSection === 'visuals'" id="admin-visuals" class="admin-visual-section">
      <div class="admin-section-heading"><div><span class="eyebrow">DATA VISUALIZATION</span><h2>数据可视化</h2></div><p>折线图与柱状图展示最近 7 天；请求、Token、费用、用户和订单均由服务端数据库聚合统计。</p></div>
      <div class="admin-dashboard-grid">
        <div class="panel chart-panel chart-wide">
          <div class="panel-head"><div><span class="eyebrow">LAST 7 DAYS</span><h2>每日请求趋势</h2></div><div class="chart-legend"><span><i class="legend-requests"></i>请求</span><span><i class="legend-tokens"></i>Token</span></div></div>
          <div v-if="weeklyDaily.length" class="daily-chart">
            <div v-for="item in weeklyDaily" :key="item.day" class="daily-column">
              <div class="daily-bars"><span class="daily-bar-item"><b>{{ compactValue(item.requests) }}</b><i class="daily-bar requests" :style="{ height: chartHeight(item.requests, dailyRequestMax) }" :title="`${item.requests || 0} 次请求`"></i></span><span class="daily-bar-item"><b>{{ compactValue(item.total_tokens) }}</b><i class="daily-bar tokens" :style="{ height: chartHeight(item.total_tokens, dailyTokenMax) }" :title="`${tokens(item.total_tokens)} Token`"></i></span></div>
              <small>{{ shortDate(item.day) }}</small>
            </div>
          </div>
          <div v-else class="empty compact-empty">暂无趋势数据</div>
        </div>
        <div class="panel chart-panel chart-wide daily-line-panel">
          <div class="panel-head"><div><span class="eyebrow">LAST 7 DAYS</span><h2>每日 Token 与费用</h2></div><div class="chart-legend"><span><i class="legend-tokens"></i>Token</span><span><i class="legend-cost"></i>费用</span></div></div>
          <div v-if="dailyLinePoints.length" class="line-chart-wrap">
            <svg class="line-chart" viewBox="0 0 720 250" role="img" aria-label="每日 Token 和费用趋势折线图" preserveAspectRatio="none">
              <g class="line-grid">
                <line v-for="y in lineGridYs" :key="y" x1="22" :y1="y" x2="700" :y2="y" />
              </g>
              <polyline class="line-series tokens" :points="linePoints('tokens')" />
              <polyline class="line-series cost" :points="linePoints('cost')" />
              <g v-for="point in dailyLinePoints" :key="point.day">
                <circle class="line-point tokens" :cx="point.x" :cy="point.tokensY" r="3"><title>{{ point.day }} · {{ tokens(point.total_tokens) }} Token · ¥{{ money(point.total_cost) }} · {{ point.requests || 0 }} 次请求</title></circle>
                <circle class="line-point cost" :cx="point.x" :cy="point.costY" r="3"><title>{{ point.day }} · ¥{{ money(point.total_cost) }} · {{ tokens(point.total_tokens) }} Token</title></circle>
                <text class="line-value-label tokens" :x="point.x" :y="Math.max(12, point.tokensY - 8)">{{ compactValue(point.total_tokens) }}</text>
                <text class="line-value-label cost" :x="point.x" :y="Math.min(220, point.costY + 14)">¥{{ compactValue(point.total_cost) }}</text>
                <text class="line-axis-label" :x="point.x" y="232">{{ shortDate(point.day) }}</text>
              </g>
            </svg>
            <div class="line-chart-scale"><span>Token 最高 {{ tokens(dailyTokenMax) }}</span><span>费用最高 ¥{{ money(dailyCostMax) }}</span></div>
          </div>
          <div v-else class="empty compact-empty">暂无 Token 和费用趋势数据</div>
        </div>
        <div class="panel chart-panel combined-user-ranking-panel">
          <div class="panel-head"><div><span class="eyebrow">USER USAGE RANKING</span><h2>用户用量排行</h2></div><small class="panel-period">当日 / 历史</small></div>
          <div class="combined-rank-grid">
            <section><h3>当日排行</h3><div v-if="analytics.today_users && analytics.today_users.length" class="rank-list"><div v-for="(item,index) in analytics.today_users" :key="`today-${item.user_id}`" class="rank-row user-rank"><div><strong><span class="rank-number">{{ index + 1 }}</span>{{ item.username }}</strong><small>{{ item.requests || 0 }} 次请求 · ¥{{ money(item.total_cost) }}</small></div><b>{{ tokens(item.total_tokens) }}</b><i class="rank-track"><em :style="{ width: valueWidth(item.total_tokens, todayUserTokenMax) + '%' }"></em></i></div></div><div v-else class="empty compact-empty">今日暂无用量</div></section>
            <section><h3>历史排行</h3><div v-if="analytics.users && analytics.users.length" class="rank-list"><div v-for="(item,index) in analytics.users" :key="`all-${item.user_id}`" class="rank-row user-rank"><div><strong><span class="rank-number">{{ index + 1 }}</span>{{ item.username }}</strong><small>{{ item.requests || 0 }} 次请求 · ¥{{ money(item.total_cost) }}</small></div><b>{{ tokens(item.total_tokens) }}</b><i class="rank-track"><em :style="{ width: valueWidth(item.total_tokens, userTokenMax) + '%' }"></em></i></div></div><div v-else class="empty compact-empty">暂无历史用量</div></section>
          </div>
        </div>
        <div class="panel chart-panel">
          <div class="panel-head"><div><span class="eyebrow">BILLING & ORDERS</span><h2>计费与订单</h2></div></div>
          <div class="split-stat-block"><div><h3>计费来源</h3><div v-for="item in analytics.billing_sources" :key="item.billing_source" class="mini-stat"><span>{{ billingSource(item.billing_source) }}</span><b>{{ tokens(item.total_tokens) }}</b><small>¥{{ money(item.total_cost) }}</small></div></div><div><h3>充值订单</h3><div v-for="item in analytics.orders" :key="item.status" class="mini-stat"><span>{{ orderStatus(item.status) }}</span><b>{{ item.orders || 0 }} 笔</b><small>¥{{ money(item.amount) }}</small></div></div></div>
        </div>
        <div class="panel chart-panel user-activity-panel">
          <div class="panel-head"><div><span class="eyebrow">LAST 7 DAYS</span><h2>用户活动量</h2></div><div class="chart-legend"><span><i class="legend-activity"></i>活跃用户</span></div></div>
          <div v-if="activityLinePoints.length" class="line-chart-wrap">
            <svg class="line-chart" viewBox="0 0 720 250" role="img" aria-label="当前用户活动量折线图" preserveAspectRatio="none">
              <g class="line-grid">
                <line v-for="y in lineGridYs" :key="y" x1="22" :y1="y" x2="700" :y2="y" />
              </g>
              <polyline class="line-series activity" :points="activityLinePath" />
              <g v-for="point in activityLinePoints" :key="point.day">
                <circle class="line-point activity" :cx="point.x" :cy="point.activeY" r="3"><title>{{ point.day }} · {{ point.active_users }} 位活跃用户 · {{ point.requests }} 次 API 调用</title></circle>
                <text class="line-value-label activity" :x="point.x" :y="Math.max(12, point.activeY - 9)">{{ point.active_users }}</text>
                <text class="line-axis-label" :x="point.x" y="232">{{ shortDate(point.day) }}</text>
              </g>
            </svg>
            <div class="line-chart-scale"><span>活跃用户最高 {{ activityUserMax }} 人</span><span>按日统计 API 调用</span></div>
          </div>
          <div v-else class="empty compact-empty">暂无用户活动数据</div>
        </div>
        <div class="panel chart-panel admin-model-usage visual-model-usage">
          <div class="panel-head">
            <div><span class="eyebrow">MODEL USAGE</span><h2>模型使用量</h2></div>
            <div class="usage-legend"><span><i class="legend-month"></i>本月</span><span><i class="legend-total"></i>历史总量</span></div>
          </div>
          <div v-if="topModelUsage.length" class="model-usage-chart">
            <div class="model-usage-plot">
              <div v-for="item in topModelUsage" :key="item.model" class="model-usage-group" :title="`${item.model}：本月 ${tokens(item.month_tokens)} Token，历史 ${tokens(item.total_tokens)} Token`">
                <div class="model-usage-bars">
                  <div class="usage-bar-group">
                    <div class="usage-bar-wrap"><small>{{ tokens(item.month_tokens) }}</small><i class="usage-bar month" :style="{ height: barWidth(item.month_tokens) }"></i></div>
                    <div class="usage-bar-wrap"><small>{{ tokens(item.total_tokens) }}</small><i class="usage-bar total" :style="{ height: barWidth(item.total_tokens) }"></i></div>
                  </div>
                </div>
                <strong class="model-usage-label">{{ item.model }}</strong>
                <small class="model-usage-meta">本月 {{ item.month_requests || 0 }} 次 · 历史 {{ item.total_requests || 0 }} 次</small>
                <small class="model-usage-cost">¥{{ money(item.month_cost) }} / ¥{{ money(item.total_cost) }}</small>
              </div>
            </div>
          </div>
          <div v-else class="empty compact-empty">暂无模型使用记录</div>
        </div>
      </div>
    </section>

    <section v-if="activeAdminSection === 'logs'" id="admin-logs" class="panel admin-log-panel">
      <div class="panel-head"><div><span class="eyebrow">RUNTIME LOGS</span><h2>后台日志</h2><p class="panel-note">仅记录警告和异常摘要，不保存请求正文；日志查询接口按 5 条分页。</p></div><div class="log-health"><span class="health-dot"></span>运行中</div></div>
      <div class="log-metric-strip"><span>警告 <b>{{ requestMetrics.persisted_warning_events || 0 }}</b></span><span>异常 <b>{{ requestMetrics.persisted_exception_events || 0 }}</b></span><span>失败率 <b>{{ Number(requestMetrics.failure_rate || 0).toFixed(2) }}%</b></span><span>运行时长 <b>{{ uptime(requestMetrics.uptime_seconds) }}</b></span></div>
      <div v-if="recentLogs.length" class="runtime-log-list"><div v-for="item in recentLogs" :key="item.id" class="runtime-log-row"><span :class="['log-level', item.level === 'ERROR' ? 'error' : 'warn']">{{ item.level === 'ERROR' ? '异常' : '警告' }}</span><div><strong>{{ item.event_type === 'EXCEPTION' ? '未处理异常' : '请求失败' }} · {{ item.message }}</strong><small>{{ item.method }} {{ item.path }} · HTTP {{ item.status_code || '-' }} · {{ item.latency_ms || 0 }}ms · {{ format(item.created_at) }}</small></div></div></div><div v-else class="empty compact-empty">暂无警告或异常日志</div>
      <div v-if="logPagination.pages > 1" class="pagination"><button class="secondary-btn" :disabled="logPagination.page <= 1" @click="changeLogPage(logPagination.page - 1)">上一页</button><span>第 {{ logPagination.page }} / {{ logPagination.pages }} 页，共 {{ logPagination.total }} 条</span><button class="secondary-btn" :disabled="logPagination.page >= logPagination.pages" @click="changeLogPage(logPagination.page + 1)">下一页</button></div>

      <div class="admin-log-conversations conversation-summary-panel">
        <div class="panel-head"><div><span class="eyebrow">ADMIN / USAGE LOGS</span><h2>调用记录</h2><p class="panel-note">仅管理员可见，显示最新 5 条调用摘要；系统只保留用户、模型、状态、Token、费用和时间。</p></div></div>
        <div class="metric-grid log-metrics">
          <div class="metric-card"><span>日志总数</span><strong>{{ summary.conversation_totals?.total_records || 0 }}</strong><small>系统记录量</small></div>
          <div class="metric-card"><span>成功调用</span><strong>{{ summary.conversation_totals?.answered_records || 0 }}</strong><small>成功返回</small></div>
          <div class="metric-card"><span>失败记录</span><strong>{{ summary.conversation_totals?.failed_records || 0 }}</strong><small>含计费失败</small></div>
          <div class="metric-card"><span>会话 Token</span><strong>{{ tokens(summary.conversation_totals?.total_tokens) }}</strong><small>累计消耗</small></div>
        </div>
        <div v-if="recentConversations.length" class="conversation-log-list">
          <div v-for="item in recentConversations" :key="item.id" class="conversation-log-row">
            <div><strong>{{ item.username || '未知用户' }}</strong><small>{{ format(item.created_at) }} · {{ item.model || '-' }} · {{ conversationProtocol(item.protocol) }}</small></div>
            <div><span>{{ conversationStatus(item.status) }}</span><small>{{ item.protocol === 'legacy_usage' ? '历史用量摘要' : '调用统计' }} · {{ tokens(item.total_tokens) }} Token · ¥{{ money(item.cost) }}</small></div>
          </div>
        </div>
        <div v-else class="empty compact-empty">暂无日志</div>
      </div>

      <div class="admin-log-export">
        <div class="panel-head"><div><span class="eyebrow">DATA EXPORT</span><h2>调用统计导出</h2><p class="panel-note">服务端生成 CSV，SQLite 与 MySQL 均可使用；不包含请求正文、图片数据或模型回答。</p></div><button class="secondary-btn" :disabled="exporting" @click="downloadExport">{{ exporting ? '导出中…' : '下载 CSV' }}</button></div>
        <div class="export-controls"><label>开始时间<input v-model="exportFilters.start_at" type="datetime-local" /></label><label>结束时间<input v-model="exportFilters.end_at" type="datetime-local" /></label><label>用户 ID<input v-model.trim="exportFilters.user_id" inputmode="numeric" placeholder="全部" /></label><label>模型<input v-model.trim="exportFilters.model" placeholder="全部模型" /></label><label>状态<select v-model="exportFilters.status"><option value="">全部</option><option value="SUCCEEDED">成功</option><option value="FAILED">失败</option><option value="BILLING_FAILED">计费失败</option></select></label></div>
        <p class="panel-note export-note">最多导出 50,000 条摘要记录。CSV 仅供管理员合规审计，请妥善保护导出文件。</p>
      </div>
    </section>

    <section v-if="activeAdminSection === 'device'" id="admin-device" class="panel device-panel">
      <div class="panel-head"><div><span class="eyebrow">DEVICE TELEMETRY</span><h2>设备信息</h2><p class="panel-note">展示当前后端主机的 CPU、内存、磁盘和网络计数。</p></div><small class="device-updated">更新于 {{ format(device.updated_at) }}</small></div>
      <div class="device-meta"><span>{{ device.host || '-' }}</span><span>{{ device.platform || '-' }}</span><span>Python {{ device.python || '-' }}</span></div>
      <div class="device-metrics-grid">
        <div class="device-meter"><div class="meter-head"><span>CPU 使用率</span><b>{{ Number(device.cpu && device.cpu.percent || 0).toFixed(1) }}%</b></div><div class="meter-track"><i class="cpu" :style="{ width: Math.min(100, Number(device.cpu && device.cpu.percent || 0)) + '%' }"></i></div><small>{{ device.cpu && device.cpu.logical || 0 }} 逻辑核心 · {{ device.cpu && device.cpu.physical || 0 }} 物理核心</small></div>
        <div class="device-meter"><div class="meter-head"><span>内存使用</span><b>{{ Number(device.memory && device.memory.percent || 0).toFixed(1) }}%</b></div><div class="meter-track"><i class="memory" :style="{ width: Math.min(100, Number(device.memory && device.memory.percent || 0)) + '%' }"></i></div><small>{{ bytes(device.memory && device.memory.used) }} / {{ bytes(device.memory && device.memory.total) }} · 进程 {{ bytes(device.memory && device.memory.process_used) }}</small></div>
        <div class="device-meter"><div class="meter-head"><span>磁盘使用</span><b>{{ Number(device.storage && device.storage.percent || 0).toFixed(1) }}%</b></div><div class="meter-track"><i class="storage" :style="{ width: Math.min(100, Number(device.storage && device.storage.percent || 0)) + '%' }"></i></div><small>{{ bytes(device.storage && device.storage.used) }} / {{ bytes(device.storage && device.storage.total) }} · 剩余 {{ bytes(device.storage && device.storage.free) }}</small></div>
        <div class="device-meter network-meter"><div class="meter-head"><span>网络吞吐</span><b>{{ bytes(device.network && device.network.bytes_recv) }} 入站</b></div><div class="network-stats"><span>出站 {{ bytes(device.network && device.network.bytes_sent) }}</span><span>收包 {{ device.network && device.network.packets_recv || 0 }}</span><span>发包 {{ device.network && device.network.packets_sent || 0 }}</span><span>丢包 {{ (device.network && device.network.drops_in || 0) + (device.network && device.network.drops_out || 0) }}</span></div></div>
      </div>
    </section>

    <div v-if="activeAdminSection === 'business'" id="admin-business-plans" class="panel subscription-admin-panel business-anchor-target">
      <div class="panel-head"><div><span class="eyebrow">SUBSCRIPTION MANAGEMENT</span><h2>套餐管理</h2><p class="panel-note">金额或 Token 未填写时按 0 处理，0 表示该项不限制。</p></div></div>
      <form class="plan-form" @submit.prevent="savePlan"><div class="plan-form-intro"><div><span class="eyebrow">{{ editingPlan ? 'EDIT PLAN' : 'NEW PLAN' }}</span><strong>{{ editingPlan ? '编辑套餐配置' : '新建套餐配置' }}</strong></div><small>套餐可将用户自动升级到指定分组</small></div><label>套餐名称<input v-model.trim="planForm.name" placeholder="例如：标准版" required /></label><label class="plan-description">套餐说明<input v-model.trim="planForm.description" placeholder="面向日常对话与代码任务" /></label><label>售价<input v-model.number="planForm.price" type="number" min="0" step="0.01" placeholder="0" /></label><label>有效天数<input v-model.number="planForm.duration_days" type="number" min="1" step="1" placeholder="30" /></label><label>每日金额<input v-model.number="planForm.daily_amount" type="number" min="0" step="0.0001" placeholder="0" /></label><label>每日 Token<input v-model.number="planForm.daily_tokens" type="number" min="0" step="1" placeholder="0" /></label><label class="plan-group-field">套餐对应分组<AppSelect v-model="planForm.group_id" :options="planGroupSelectOptions" aria-label="套餐对应分组" /><small>套餐可用时按权重自动升级；失效后自动降级。</small></label><div class="plan-form-actions"><button class="primary-btn" :disabled="planSaving">{{ planSaving ? '保存中…' : editingPlan ? '保存套餐' : '新建套餐' }}<span class="plan-action-arrow">→</span></button><button v-if="editingPlan" type="button" class="secondary-btn" @click="resetPlanForm">取消编辑</button></div></form>
      <div v-if="plans.length" class="admin-plan-grid"><article v-for="plan in plans" :key="plan.id" class="admin-plan-card"><div class="admin-plan-top"><div><span class="plan-id">PLAN {{ String(plan.id).padStart(2, '0') }}</span><h3>{{ plan.name }}</h3></div><span :class="['status', plan.enabled ? 'success' : 'pending']">{{ plan.enabled ? '启用' : '停用' }}</span></div><p>{{ plan.description || '未填写套餐说明' }}</p><div class="admin-plan-price"><strong>¥{{ Number(plan.price || 0).toFixed(2) }}</strong><span>/ {{ plan.duration_days }} 天</span></div><div class="admin-plan-group"><small>生效分组</small><b>{{ plan.group_name || '不调整分组' }}</b><span v-if="plan.group_name">权重 {{ plan.group_weight }}</span></div><div class="admin-plan-specs"><div><small>每日金额</small><b>{{ plan.daily_amount > 0 ? `¥${Number(plan.daily_amount).toFixed(4)}` : '不限' }}</b></div><div><small>每日 Token</small><b>{{ plan.daily_tokens > 0 ? Number(plan.daily_tokens).toLocaleString('zh-CN') : '不限' }}</b></div></div><div class="admin-plan-actions"><button class="secondary-btn" @click="editPlan(plan)">编辑</button><button class="secondary-btn" @click="togglePlan(plan)">{{ plan.enabled ? '停用' : '启用' }}</button><button v-if="plan.enabled" class="text-btn danger" @click="removePlan(plan)">停用并保留历史</button></div></article></div><div v-else class="empty compact-empty">暂无套餐，请先新建</div>
      <div v-if="planPagination.pages > 1" class="pagination"><button class="secondary-btn" :disabled="planPagination.page <= 1" @click="changePlanPage(planPagination.page - 1)">上一页</button><span>第 {{ planPagination.page }} / {{ planPagination.pages }} 页，共 {{ planPagination.total }} 条</span><button class="secondary-btn" :disabled="planPagination.page >= planPagination.pages" @click="changePlanPage(planPagination.page + 1)">下一页</button></div>
    </div>

    <div v-if="activeAdminSection === 'business'" id="admin-business-codes" class="panel recharge-code-panel business-anchor-target">
      <div class="panel-head"><div><span class="eyebrow">RECHARGE CODES</span><h2>兑换码</h2></div></div>
      <div class="recharge-code-form">
        <div class="recharge-code-intro">
          <div><span class="eyebrow">NEW RECHARGE CODES</span><strong>批量生成兑换码</strong></div>
          <small>默认 48 小时，可自定义</small>
        </div>
        <label class="recharge-code-field">
          <span>面值（元）</span>
          <input v-model.number="codeForm.amount" type="number" min="0.01" step="0.01" placeholder="请输入面值" />
        </label>
        <label class="recharge-code-field">
          <span>数量</span>
          <input v-model.number="codeForm.count" type="number" min="1" max="500" placeholder="请输入数量" />
        </label>
        <label class="recharge-code-field">
          <span>有效期 <small>1 小时至 365 天</small></span>
          <div class="recharge-expiry-input"><input v-model.number="codeForm.expire_hours" type="number" min="1" max="8760" step="1" placeholder="48" /><em>小时</em></div>
        </label>
        <div class="recharge-code-actions"><button class="primary-btn" :disabled="generating" @click="generate">{{ generating ? '生成中…' : '批量生成' }}<span class="plan-action-arrow">→</span></button></div>
      </div>
      <div v-if="newCodes.length" class="created-key recharge-created">
        <div class="created-key-head"><p>本次生成的兑换码（有效期至 {{ format(newCodes[0].expires_at) }}）</p><button class="secondary-btn" @click="copyAllNew">{{ allNewCopied ? '已复制全部' : '批量复制' }}</button></div>
        <div v-for="item in newCodes" :key="item.id" class="code-row"><code>{{ item.code }}</code><span>¥{{ Number(item.amount).toFixed(2) }}</span><span class="copy-state" :class="{ copied: newCopied[item.id] }">{{ newCopied[item.id] ? '已复制' : '未复制' }}</span><button class="text-btn" @click="copyNew(item)">{{ newCopied[item.id] ? '再次复制' : '复制' }}</button></div>
      </div>
      <div class="table-wrap"><table><thead><tr><th>兑换码</th><th>面值</th><th>状态</th><th>创建时间</th><th>有效期至</th><th>使用信息</th><th>操作</th></tr></thead><tbody><tr v-for="item in codes" :key="item.id"><td><code>{{ item.code || '历史兑换码不可显示' }}</code></td><td>¥{{ Number(item.amount || 0).toFixed(2) }}</td><td><span :class="['status', codeStatusClass(item.status)]">{{ codeStatus(item.status) }}</span></td><td>{{ format(item.created_at) }}</td><td>{{ format(item.expires_at) }}</td><td>{{ item.status === 'REDEEMED' ? `已使用${item.redeemed_at ? ' · ' + format(item.redeemed_at) : ''}` : item.status === 'ACTIVE' ? '未使用' : '-' }}</td><td><button v-if="item.code" class="text-btn" @click="copyCode(item)">{{ copiedCodes[item.id] ? '已复制' : '复制' }}</button><button v-if="item.status === 'ACTIVE'" class="text-btn danger" @click="revoke(item)">撤销</button></td></tr></tbody></table></div>
      <div v-if="!codes.length" class="empty compact-empty">暂无兑换码</div>
      <div class="pagination" v-if="codePagination.pages > 1"><button class="secondary-btn" :disabled="codePagination.page <= 1" @click="changeCodePage(codePagination.page - 1)">上一页</button><span>第 {{ codePagination.page }} / {{ codePagination.pages }} 页，共 {{ codePagination.total }} 条</span><button class="secondary-btn" :disabled="codePagination.page >= codePagination.pages" @click="changeCodePage(codePagination.page + 1)">下一页</button></div>
    </div>

    <div v-if="activeAdminSection === 'business'" id="admin-business-groups" class="panel user-group-panel business-anchor-target">
      <div class="panel-head user-group-panel-head">
        <div><span class="eyebrow">USER GROUP POLICY</span><h2>用户分组</h2><p class="panel-note">为不同用户配置独立的模型权限与并发策略，超限请求会自动进入队列。</p></div>
        <div class="group-overview"><span><b>{{ groupPagination.total || groups.length }}</b><small>分组总数</small></span><span><b>{{ groupMemberTotal }}</b><small>已分组成员</small></span></div>
      </div>
      <form :class="['user-group-form', { editing: editingGroup }]" @submit.prevent="saveGroup">
        <div class="group-form-banner">
          <div class="group-form-mark">{{ editingGroup ? 'ED' : '+' }}</div>
          <div><span class="eyebrow">{{ editingGroup ? 'EDIT GROUP' : 'CREATE GROUP' }}</span><strong>{{ editingGroup ? `编辑「${editingGroup.name}」` : '创建新的访问策略' }}</strong><small>并发上限按每位成员单独计算，不会由全组成员共同占用。</small></div>
          <span class="group-form-state">{{ editingGroup ? '编辑模式' : '新建模式' }}</span>
        </div>
        <div class="group-form-content">
          <div class="form-grid group-basic-fields"><label><span>分组名称</span><input v-model.trim="groupForm.name" maxlength="120" placeholder="例如：默认用户组" required /></label><label><span>分组权重</span><div class="group-concurrency-input"><input v-model.number="groupForm.weight" type="number" min="0" max="10000" step="1" required /><em>高权重优先</em></div></label><label><span>每人并发数</span><div class="group-concurrency-input"><input v-model.number="groupForm.concurrency_limit" type="number" min="1" max="100" step="1" required /><em>个任务</em></div></label></div>
          <label class="group-description-field"><span>分组说明</span><input v-model.trim="groupForm.description" maxlength="500" placeholder="简单说明该组的用途和适用人群" /></label>
          <label class="group-default-toggle"><input v-model="groupForm.is_default" type="checkbox" /><i aria-hidden="true"></i><span><b>设为注册默认组</b><small>以后注册的普通用户将自动进入该组</small></span></label>
          <div class="group-model-picker">
            <div class="group-model-head"><div><span class="eyebrow">MODEL ACCESS</span><strong>允许使用的模型</strong></div><label class="group-all-models"><input v-model="groupForm.allow_all" type="checkbox" /><i aria-hidden="true"></i><span>全部模型</span></label></div>
            <div v-if="!groupForm.allow_all" class="group-model-options"><label v-for="model in modelCatalog" :key="model" :class="{ selected: groupForm.allowed_models.includes(model) }"><input v-model="groupForm.allowed_models" type="checkbox" :value="model" /><span>{{ model }}</span></label><span v-if="!modelCatalog.length" class="muted">尚未读取到模型目录</span></div>
            <div class="group-model-foot"><span>{{ groupForm.allow_all ? '当前允许全部模型，并自动包含以后新增的模型' : `已选择 ${groupForm.allowed_models.length} / ${modelCatalog.length} 个模型` }}</span><small v-if="!groupForm.allow_all && !groupForm.allowed_models.length">未选择模型时，该组成员将不能发起模型请求</small></div>
          </div>
        </div>
        <div class="group-form-actions"><button class="primary-btn" :disabled="groupSaving"><span>{{ groupSaving ? '保存中…' : editingGroup ? '保存修改' : '创建分组' }}</span><b aria-hidden="true">→</b></button><button v-if="editingGroup" type="button" class="secondary-btn" @click="resetGroupForm">取消编辑</button></div>
      </form>
      <div class="group-list-head"><div><span class="eyebrow">POLICY DIRECTORY</span><strong>已配置分组</strong></div><small>默认组不可删除；删除其他分组时，成员会转入默认组。</small></div>
      <div class="user-group-grid"><article v-for="group in groups" :key="group.id" :class="['user-group-card', { default: group.is_default, editing: editingGroup && editingGroup.id === group.id }]">
        <div class="group-card-accent"></div>
        <div class="group-card-top"><div class="group-card-mark">{{ String(group.name || '组').slice(0, 1).toUpperCase() }}</div><div class="group-card-identity"><span class="eyebrow">GROUP {{ String(group.id).padStart(2, '0') }}</span><h3><router-link class="group-detail-link" :to="`/admin/user-groups/${group.id}`" :aria-label="`查看${group.name}的成员和用量`">{{ group.name }} <span aria-hidden="true">↗</span></router-link></h3></div><span v-if="group.is_default" class="group-default-badge"><i></i>注册默认</span></div>
        <p class="group-card-description">{{ group.description || '暂未填写分组说明，可通过编辑补充分组用途。' }}</p>
        <div class="group-policy-stats"><span><small>权重等级</small><b>{{ group.weight }}</b></span><span><small>基础成员</small><b>{{ group.member_count || 0 }}<em> 人</em></b></span><span><small>绑定套餐</small><b>{{ group.plan_count || 0 }}<em> 个</em></b></span><span><small>单人并发</small><b>{{ group.concurrency_limit }}<em> 个任务</em></b></span></div>
        <div class="group-model-summary"><span v-for="model in groupModelChips(group)" :key="model">{{ model }}</span><span v-if="groupExtraModelCount(group)" class="more">+{{ groupExtraModelCount(group) }}</span><span v-if="!groupModelChips(group).length" class="blocked">未开放模型</span></div>
        <div class="group-card-actions"><button class="secondary-btn" @click="editGroup(group)">{{ editingGroup && editingGroup.id === group.id ? '正在编辑' : '编辑策略' }}</button><button v-if="!group.is_default" class="text-btn danger" @click="removeGroup(group)">删除分组</button><span v-else class="group-protected-note">受保护分组</span></div>
      </article></div>
      <div v-if="!groups.length" class="empty compact-empty">暂无用户组</div>
      <div v-if="groupPagination.pages > 1" class="pagination"><button class="secondary-btn" :disabled="groupPagination.page <= 1" @click="changeGroupPage(groupPagination.page - 1)">上一页</button><span>第 {{ groupPagination.page }} / {{ groupPagination.pages }} 页，共 {{ groupPagination.total }} 个分组</span><button class="secondary-btn" :disabled="groupPagination.page >= groupPagination.pages" @click="changeGroupPage(groupPagination.page + 1)">下一页</button></div>
    </div>

    <div v-if="activeAdminSection === 'business'" id="admin-business-users" class="panel business-anchor-target">
      <div class="panel-head"><div><span class="eyebrow">USER DIRECTORY</span><h2>用户列表</h2></div><div class="admin-user-actions"><div class="search small-search"><input v-model="keyword" @keyup.enter="loadUsers" placeholder="搜索账户或邮箱" /><button @click="loadUsers">查询</button></div><button class="primary-btn" @click="openCreateUser">新建用户</button></div></div>
      <div class="table-wrap"><table><thead><tr><th>账户</th><th>当前生效分组</th><th>角色</th><th>余额</th><th>状态</th><th>最后登录</th><th>操作</th></tr></thead><tbody><tr v-for="item in users" :key="item.id"><td>{{ item.username }}<small>{{ item.email || '未设置邮箱' }}</small></td><td>{{ item.group_name || '未分组' }} · 权重 {{ item.group_weight || 0 }}<small>{{ item.group_source === 'SUBSCRIPTION' ? `套餐升级：${item.group_source_plan_name}` : `基础分组：${item.assigned_group_name || item.group_name}` }} · 并发 {{ item.group_concurrency_limit || 1 }}</small></td><td>{{ item.role }}</td><td>¥{{ Number(item.balance || 0).toFixed(4) }}</td><td><span :class="['status', item.enabled ? 'success' : 'pending']">{{ item.enabled ? '正常' : '停用' }}</span></td><td>{{ item.last_login ? format(item.last_login) : '从未登录' }}</td><td><router-link class="text-btn" :to="`/admin/users/${item.id}`">查看</router-link></td></tr></tbody></table></div>
      <div v-if="!users.length" class="empty compact-empty">暂无用户</div>
      <div class="pagination" v-if="userPagination.pages > 1"><button class="secondary-btn" :disabled="userPagination.page <= 1" @click="changeUserPage(userPagination.page - 1)">上一页</button><span>第 {{ userPagination.page }} / {{ userPagination.pages }} 页，共 {{ userPagination.total }} 条</span><button class="secondary-btn" :disabled="userPagination.page >= userPagination.pages" @click="changeUserPage(userPagination.page + 1)">下一页</button></div>
    </div>

    <div v-if="createUserOpen" class="modal-backdrop" @click.self="closeCreateUser"><div ref="createUserDialog" class="modal-card admin-create-user" role="dialog" aria-modal="true" aria-labelledby="create-user-title" tabindex="-1" @keydown="trapCreateFocus" @keydown.esc.stop="closeCreateUser"><button class="modal-close" aria-label="关闭" @click="closeCreateUser">×</button><span class="eyebrow">NEW USER</span><h2 id="create-user-title">{{ createdUserResult ? '用户创建成功' : '新建用户' }}</h2><template v-if="createdUserResult"><p class="panel-note">首枚密钥明文只显示这一次，请立即交给用户或下载配置文件。</p><div class="created-user-key"><span>{{ createdUserResult.user.username }} · 默认密钥</span><code>{{ createdUserResult.apiKey }}</code></div><div class="feedback-actions created-user-actions"><button type="button" class="secondary-btn" @click="copy(createdUserResult.apiKey)">复制密钥</button><button type="button" class="primary-btn" @click="downloadCreatedUserConfig">下载 config.toml</button><button type="button" class="secondary-btn" @click="closeCreateUser">完成</button></div></template><form v-else @submit.prevent="createUser"><p class="panel-note">创建后自动生成首枚 API 密钥，明文仅在创建成功后显示一次。</p><div class="form-grid"><label>账户名<input v-model.trim="userForm.username" autocomplete="off" maxlength="32" placeholder="3-32 位字符" autofocus /></label><label>初始余额<input v-model.number="userForm.balance" type="number" min="-0.1" step="0.0001" /></label></div><label>登录密码<input v-model="userForm.password" type="password" autocomplete="new-password" minlength="6" placeholder="至少 6 位字符" /></label><label>邮箱 <span class="optional">可选</span><input v-model.trim="userForm.email" type="email" autocomplete="off" placeholder="仅用于账户资料，暂不发送通知" /></label><div class="form-grid"><label>角色<AppSelect v-model="userForm.role" :options="roleSelectOptions" aria-label="用户角色" /></label><label>用户分组<AppSelect v-model="userForm.group_id" :options="groupSelectOptions" aria-label="用户分组" /></label></div><label class="create-toggle"><span>创建后启用</span><input v-model="userForm.enabled" type="checkbox" /></label><p v-if="createError" class="form-error">{{ createError }}</p><div class="feedback-actions"><button type="button" class="secondary-btn" @click="closeCreateUser">取消</button><button type="submit" class="primary-btn" :disabled="creatingUser">{{ creatingUser ? '创建中…' : '创建用户' }}</button></div></form></div></div>
  </section>
</template>

<script>
import { api } from '../api'
import { copyToClipboard, notify, askConfirm, focusDialog, trapDialogFocus } from '../ui'
import AppSelect from '../components/AppSelect.vue'
import { buildCodexConfig, downloadTextFile } from '../config/codex'

const emptyGroup = () => ({ name: '', description: '', weight: 10, concurrency_limit: 1, is_default: false, allow_all: true, allowed_models: [] })

export default {
  components: { AppSelect },
  props: { appName: String, apiBaseUrl: String, codexConfig: Object },
  data: () => ({
    summary: {},
    activeAdminSection: 'business',
    businessAnchor: 'plans',
    modelUsage: [],
    recentConversations: [],
    analytics: { daily: [], activity: [], today_users: [], users: [], statuses: [], billing_sources: [], orders: [] },
    requestMetrics: {},
    recentLogs: [],
    device: { cpu: {}, memory: {}, storage: {}, network: {} },
    plans: [],
    users: [],
    groups: [],
    groupOptions: [],
    modelCatalog: [],
    keyword: '',
    codes: [],
    newCodes: [],
    newCopied: {},
    copiedCodes: {},
    allNewCopied: false,
    codeForm: { amount: 10, count: 1, expire_hours: 48 },
    generating: false,
    userPagination: { page: 1, pages: 1, total: 0 },
    codePagination: { page: 1, pages: 1, total: 0 },
    planPagination: { page: 1, pages: 1, total: 0 },
    groupPagination: { page: 1, pages: 1, total: 0 },
    logPagination: { page: 1, pages: 1, total: 0, page_size: 5 },
    planForm: { name: '', description: '', price: 0, duration_days: 30, daily_amount: 0, daily_tokens: 0, group_id: null, enabled: true },
    editingPlan: null,
    planSaving: false,
    groupForm: emptyGroup(),
    editingGroup: null,
    groupSaving: false,
    createUserOpen: false,
    creatingUser: false,
    createError: '',
    createdUserResult: null,
    userForm: { username: '', password: '', email: '', role: 'USER', balance: 0, enabled: true, group_id: null },
    loadingSections: false,
    exportFilters: { start_at: '', end_at: '', user_id: '', model: '', status: '' },
    exporting: false,
    dialogReturnFocus: null
  }),
  computed: {
    topModelUsage () {
      return [...this.modelUsage]
        .sort((left, right) => Number(right.total_tokens || 0) - Number(left.total_tokens || 0) || String(left.model || '').localeCompare(String(right.model || '')))
        .slice(0, 4)
    },
    modelUsageMax () {
      return Math.max(1, ...this.topModelUsage.map(item => Number(item.total_tokens || 0)))
    },
    weeklyDaily () {
      return (this.analytics.daily || []).slice(-7)
    },
    weeklyActivity () {
      return (this.analytics.activity || []).slice(-7)
    },
    dailyRequestMax () {
      return Math.max(1, ...this.weeklyDaily.map(item => Number(item.requests || 0)))
    },
    dailyTokenMax () {
      return Math.max(1, ...this.weeklyDaily.map(item => Number(item.total_tokens || 0)))
    },
    dailyCostMax () {
      return Math.max(0.0001, ...this.weeklyDaily.map(item => Number(item.total_cost || 0)))
    },
    dailyLinePoints () {
      const items = this.weeklyDaily
      const left = 22
      const width = 678
      const top = 18
      const height = 186
      const span = Math.max(1, items.length - 1)
      return items.map((item, index) => {
        const totalTokens = Number(item.total_tokens || 0)
        const totalCost = Number(item.total_cost || 0)
        return {
          ...item,
          total_tokens: totalTokens,
          total_cost: totalCost,
          x: left + (width * index / span),
          tokensY: top + height * (1 - totalTokens / this.dailyTokenMax),
          costY: top + height * (1 - totalCost / this.dailyCostMax)
        }
      })
    },
    lineGridYs () {
      return [18, 64.5, 111, 157.5, 204]
    },
    userTokenMax () {
      return Math.max(1, ...((this.analytics.users || []).map(item => Number(item.total_tokens || 0))))
    },
    todayUserTokenMax () {
      return Math.max(1, ...((this.analytics.today_users || []).map(item => Number(item.total_tokens || 0))))
    },
    activityUserMax () {
      return Math.max(1, ...this.weeklyActivity.map(item => Number(item.active_users || 0)))
    },
    activityLinePoints () {
      const items = this.weeklyActivity
      const left = 22
      const width = 678
      const top = 18
      const height = 186
      const span = Math.max(1, items.length - 1)
      return items.map((item, index) => {
        const activeUsers = Number(item.active_users || 0)
        return {
          ...item,
          active_users: activeUsers,
          requests: Number(item.requests || 0),
          x: left + (width * index / span),
          activeY: top + height * (1 - activeUsers / this.activityUserMax)
        }
      })
    },
    activityLinePath () {
      return this.activityLinePoints.map(point => `${point.x},${point.activeY}`).join(' ')
    },
    exportDescription () { return '服务端 CSV 导出' },
    groupMemberTotal () { return this.groupOptions.reduce((sum, group) => sum + Number(group.member_count || 0), 0) },
    roleSelectOptions () { return [{ value: 'USER', label: '普通用户', description: '使用控制台与 API' }, { value: 'ADMIN', label: '管理员', description: '可进入管理后台' }] },
    groupSelectOptions () { return this.groupOptions.map(group => { const models = group.allowed_models || []; return { value: group.id, label: group.name, description: `并发 ${group.concurrency_limit} · ${models.includes('*') ? '全部模型' : `${models.length} 个模型`}` } }) },
    planGroupSelectOptions () { return [{ value: null, label: '不调整用户分组', description: '保持用户当前基础分组' }, ...this.groupOptions.map(group => ({ value: group.id, label: group.name, description: `权重 ${group.weight} · 并发 ${group.concurrency_limit}` }))] }
  },
  created () { this.load(); this.loadGroupOptions(); this.loadModelCatalog() },
  mounted () { if (this.$route.hash === '#admin-business-groups') this.scrollToBusinessSection('groups') },
  methods: {
    scrollToBusinessSection (section) {
      this.activeAdminSection = 'business'
      this.businessAnchor = section
      this.$nextTick(() => {
        const target = document.getElementById(`admin-business-${section}`)
        if (!target) return
        const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
        target.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' })
      })
    },
    async load () {
      this.loadingSections = true
      const results = await Promise.allSettled([
          api.adminSummary(),
          api.users(this.keyword, { page: this.userPagination.page, page_size: 5 }),
          api.rechargeCodes({ page: this.codePagination.page, page_size: 5 }),
          api.adminSubscriptionPlans({ page: this.planPagination.page, page_size: 5 }),
          api.adminLogs({ page: this.logPagination.page, page_size: 5 }),
          api.userGroups({ page: this.groupPagination.page, page_size: 5 })
      ])
      const [sResult, uResult, cResult, pResult, lResult, gResult] = results
      if (sResult.status === 'fulfilled') {
        const s = sResult.value
        this.summary = s.summary || {}
        this.modelUsage = this.summary.model_usage || []
        this.recentConversations = this.summary.recent_conversations || []
        this.analytics = this.summary.analytics || this.analytics
        this.requestMetrics = this.summary.request_metrics || {}
        this.device = this.summary.device || this.device
      }
      if (uResult.status === 'fulfilled') { const u = uResult.value; this.users = u.users || []; this.userPagination = u.pagination || this.userPagination }
      if (cResult.status === 'fulfilled') { const c = cResult.value; this.codes = c.codes || []; this.codePagination = c.pagination || this.codePagination }
      if (pResult.status === 'fulfilled') { const p = pResult.value; this.plans = p.plans || []; this.planPagination = p.pagination || this.planPagination }
      if (lResult.status === 'fulfilled') { const l = lResult.value; this.recentLogs = l.logs || this.summary.recent_logs || []; this.logPagination = l.pagination || this.logPagination }
      if (gResult.status === 'fulfilled') { const g = gResult.value; this.groups = g.groups || []; this.groupPagination = g.pagination || this.groupPagination }
      const failed = results.filter(item => item.status === 'rejected')
      if (failed.length) notify(`${failed.length} 个管理区块加载失败，其余数据已保留`, 'error')
      this.loadingSections = false
    },
    async loadUsers () {
      this.userPagination.page = 1
      const d = await api.users(this.keyword, { page: 1, page_size: 5 })
      this.users = d.users || []
      this.userPagination = d.pagination || this.userPagination
    },
    changeUserPage (page) { this.userPagination.page = page; this.load() },
    changeCodePage (page) { this.codePagination.page = page; this.load() },
    changePlanPage (page) { this.planPagination.page = page; this.load() },
    changeGroupPage (page) { this.groupPagination.page = page; this.load() },
    changeLogPage (page) { this.logPagination.page = page; this.load() },
    resetPlanForm () { this.editingPlan = null; this.planForm = { name: '', description: '', price: 0, duration_days: 30, daily_amount: 0, daily_tokens: 0, group_id: null, enabled: true } },
    resetGroupForm () { this.editingGroup = null; this.groupForm = emptyGroup() },
    editGroup (group) { const models = group.allowed_models || []; this.editingGroup = group; this.groupForm = { name: group.name, description: group.description || '', weight: Number(group.weight || 0), concurrency_limit: Number(group.concurrency_limit || 1), is_default: !!group.is_default, allow_all: models.includes('*'), allowed_models: models.filter(model => model !== '*') } },
    groupModelsLabel (group) { const models = group.allowed_models || []; return models.includes('*') ? '全部模型' : `${models.length} 个模型` },
    groupModelChips (group) { const models = group.allowed_models || []; return models.includes('*') ? ['全部模型'] : models.slice(0, 3) },
    groupExtraModelCount (group) { const models = group.allowed_models || []; return models.includes('*') ? 0 : Math.max(0, models.length - 3) },
    async saveGroup () { this.groupSaving = true; try { const body = { name: this.groupForm.name, description: this.groupForm.description, weight: this.groupForm.weight, concurrency_limit: this.groupForm.concurrency_limit, is_default: this.groupForm.is_default, allowed_models: this.groupForm.allow_all ? ['*'] : this.groupForm.allowed_models }; const d = this.editingGroup ? await api.updateUserGroup(this.editingGroup.id, body) : await api.createUserGroup(body); if (!d.ok) throw new Error(d.message); notify(this.editingGroup ? '用户组已更新' : '用户组已创建', 'success'); this.resetGroupForm(); await this.load(); await this.loadGroupOptions() } catch (e) { notify(e.message, 'error') } finally { this.groupSaving = false } },
    async removeGroup (group) { if (!await askConfirm(`删除用户组「${group.name}」后，组内成员会转入默认组，确定继续吗？`)) return; try { const d = await api.deleteUserGroup(group.id); if (!d.ok) throw new Error(d.message); await this.load(); await this.loadGroupOptions(); notify('用户组已删除，成员已转入默认组', 'success') } catch (e) { notify(e.message, 'error') } },
    async loadGroupOptions () { try { const all = []; let page = 1; let pages = 1; do { const d = await api.userGroups({ page, page_size: 5 }); all.push(...(d.groups || [])); pages = Number(d.pagination?.pages || 1); page += 1 } while (page <= pages); this.groupOptions = all } catch (e) { notify('用户组选项加载失败', 'error') } },
    async loadModelCatalog () { try { const all = []; let page = 1; let pages = 1; do { const d = await api.monitoring({ page, page_size: 12 }); all.push(...((d.models || []).map(item => item.id))); pages = Number(d.pagination?.pages || 1); page += 1 } while (page <= pages); this.modelCatalog = [...new Set(all)] } catch (e) { notify('完整模型目录加载失败', 'error') } },
    editPlan (plan) { this.editingPlan = plan; this.planForm = { name: plan.name, description: plan.description || '', price: Number(plan.price || 0), duration_days: Number(plan.duration_days || 30), daily_amount: Number(plan.daily_amount || 0), daily_tokens: Number(plan.daily_tokens || 0), group_id: plan.group_id == null ? null : Number(plan.group_id), enabled: !!plan.enabled } },
    async savePlan () {
      this.planSaving = true
      try {
        const d = this.editingPlan ? await api.updateSubscriptionPlan(this.editingPlan.id, this.planForm) : await api.createSubscriptionPlan(this.planForm)
        if (!d.ok) throw new Error(d.message)
        notify(this.editingPlan ? '套餐已更新' : '套餐已创建', 'success'); this.resetPlanForm(); await this.load()
      } catch (e) { notify(e.message, 'error') } finally { this.planSaving = false }
    },
    async togglePlan (plan) {
      try { const d = await api.updateSubscriptionPlan(plan.id, { enabled: !plan.enabled }); if (!d.ok) throw new Error(d.message); await this.load(); notify('套餐状态已更新', 'success') } catch (e) { notify(e.message, 'error') }
    },
    async removePlan (plan) {
      if (!await askConfirm(`确定停用套餐「${plan.name}」吗？历史订阅和用量会保留。`)) return
      try { const d = await api.deleteSubscriptionPlan(plan.id); if (!d.ok) throw new Error(d.message); await this.load(); notify('套餐已停用，历史记录已保留', 'success') } catch (e) { notify(e.message, 'error') }
    },
    barWidth (value) { return `${Math.max(2, Number(value || 0) / this.modelUsageMax * 100)}%` },
    linePoints (series) { return this.dailyLinePoints.map(point => `${point.x},${series === 'cost' ? point.costY : point.tokensY}`).join(' ') },
    valueWidth (value, max) { return Math.max(2, Math.min(100, Number(value || 0) / Math.max(1, Number(max || 1)) * 100)) },
    chartHeight (value, max) { return `${Math.max(4, this.valueWidth(value, max))}%` },
    tokens (value) { return Number(value || 0).toLocaleString('zh-CN') },
    money (value) { return Number(value || 0).toFixed(4) },
    compactValue (value) {
      const amount = Number(value || 0)
      const absolute = Math.abs(amount)
      if (absolute >= 1000000000) return `${(amount / 1000000000).toFixed(1).replace(/\.0$/, '')}B`
      if (absolute >= 1000000) return `${(amount / 1000000).toFixed(1).replace(/\.0$/, '')}M`
      if (absolute >= 1000) return `${(amount / 1000).toFixed(1).replace(/\.0$/, '')}K`
      if (Number.isInteger(amount)) return String(amount)
      return amount.toFixed(absolute < 1 ? 4 : 2).replace(/0+$/, '').replace(/\.$/, '')
    },
    bytes (value) {
      const amount = Number(value || 0)
      if (!amount) return '0 B'
      const units = ['B', 'KB', 'MB', 'GB', 'TB']
      const index = Math.min(units.length - 1, Math.floor(Math.log(amount) / Math.log(1024)))
      return `${(amount / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`
    },
    shortDate (value) { return value ? String(value).slice(5, 10).replace('-', '/') : '-' },
    uptime (value) {
      const seconds = Math.max(0, Number(value || 0))
      if (seconds < 60) return `${seconds.toFixed(0)} 秒`
      if (seconds < 3600) return `${(seconds / 60).toFixed(0)} 分钟`
      return `${(seconds / 3600).toFixed(1)} 小时`
    },
    statusWidth (value) {
      const max = Math.max(1, ...((this.analytics.statuses || []).map(item => Number(item.requests || 0))))
      return Math.max(4, Math.min(100, Number(value || 0) / max * 100)).toFixed(0)
    },
    usageStatus (value) { return ({ SUCCEEDED: '成功', FAILED: '失败', BILLING_FAILED: '计费失败', PROCESSING: '处理中' })[value] || value || '-' },
    billingSource (value) { return ({ FREE: '免费额度', SUBSCRIPTION: '套餐', WALLET: '钱包' })[value] || value || '钱包' },
    orderStatus (value) { return ({ PAID: '已支付', PENDING: '待支付', CANCELLED: '已取消', CODE: '兑换码' })[value] || value || '-' },
    conversationStatus (status) { return ({ SUCCEEDED: '成功', FAILED: '失败', BILLING_FAILED: '计费失败', PROCESSING: '处理中' })[status] || status || '-' },
    conversationProtocol (protocol) { return ({ chat_completions: 'Chat Completions', responses: 'Responses', internal: '内部代理', legacy_usage: '历史用量' })[protocol] || protocol || '-' },
    codeStatus (status) { return ({ ACTIVE: '未使用', REDEEMED: '已使用', REVOKED: '已撤销', EXPIRED: '已过期' })[status] || status || '-' },
    codeStatusClass (status) { return status === 'ACTIVE' ? 'success' : status === 'REDEEMED' ? 'pending' : 'cancelled' },
    format (value) { return value ? new Date(value).toLocaleString('zh-CN') : '-' },
    async writeClipboard (value) { const copied = await copyToClipboard(value); if (!copied) notify('复制失败，请检查浏览器权限', 'error'); return copied },
    async copy (value) { if (await this.writeClipboard(value)) notify('已复制', 'success') },
    async downloadExport () { this.exporting = true; try { const params = { ...this.exportFilters }; if (params.start_at) params.start_at = new Date(params.start_at).toISOString(); if (params.end_at) params.end_at = new Date(params.end_at).toISOString(); const data = await api.exportUsage(params); const blob = new Blob([data.csv || ''], { type: 'text/csv;charset=utf-8' }); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = data.filename || 'usage-export.csv'; document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url); notify(`已导出 ${data.count || 0} 条记录`, 'success') } catch (e) { notify(e.message, 'error') } finally { this.exporting = false } },
    async copyCode (item) { if (await this.writeClipboard(item.code)) { this.$set(this.copiedCodes, item.id, true); notify('兑换码已复制', 'success') } },
    async copyNew (item) { if (await this.writeClipboard(item.code)) { this.$set(this.newCopied, item.id, true); this.allNewCopied = this.newCodes.every(code => this.newCopied[code.id]); notify('兑换码已复制', 'success') } },
    async copyAllNew () { const text = this.newCodes.map(item => item.code).filter(Boolean).join('\n'); if (await this.writeClipboard(text)) { this.newCodes.forEach(item => this.$set(this.newCopied, item.id, true)); this.allNewCopied = true; notify('已复制全部兑换码', 'success') } },
    async generate () {
      const expireHours = Number(this.codeForm.expire_hours)
      if (!Number.isInteger(expireHours) || expireHours < 1 || expireHours > 8760) {
        notify('有效期必须是 1-8760 之间的整数小时', 'error')
        return
      }
      this.generating = true
      try {
        const d = await api.createRechargeCodes({ ...this.codeForm, expire_hours: expireHours })
        if (!d.ok) throw new Error(d.message)
        this.newCodes = d.codes || []
        this.newCopied = {}
        this.allNewCopied = false
        await this.load()
        notify(`兑换码已生成，有效期 ${d.expire_hours || expireHours} 小时`, 'success')
      } catch (e) { notify(e.message, 'error') } finally { this.generating = false }
    },
    async revoke (item) { if (!await askConfirm('确定撤销该兑换码吗？')) return; try { const d = await api.revokeRechargeCode(item.id); if (!d.ok) throw new Error(d.message); await this.load(); notify('兑换码已撤销', 'success') } catch (e) { notify(e.message, 'error') } },
    openCreateUser () { this.dialogReturnFocus = document.activeElement; this.createError = ''; this.createdUserResult = null; const defaultGroup = this.groupOptions.find(group => group.is_default) || this.groupOptions[0]; this.userForm = { username: '', password: '', email: '', role: 'USER', balance: 0, enabled: true, group_id: defaultGroup?.id || null }; this.createUserOpen = true; this.$nextTick(() => focusDialog(this.$refs.createUserDialog)) },
    closeCreateUser () { if (!this.creatingUser) { this.createUserOpen = false; this.createdUserResult = null; this.$nextTick(() => this.dialogReturnFocus && this.dialogReturnFocus.focus && this.dialogReturnFocus.focus()) } },
    trapCreateFocus (event) { trapDialogFocus(event, this.$refs.createUserDialog) },
    downloadCreatedUserConfig () { if (!this.createdUserResult?.apiKey) return; const content = buildCodexConfig({ appName: this.appName, apiBaseUrl: this.apiBaseUrl, codexConfig: this.codexConfig, apiKey: this.createdUserResult.apiKey }); downloadTextFile('config.toml', content); notify('用户 config.toml 已下载', 'success') },
    async createUser () { this.createError = ''; if (this.userForm.username.length < 3) return (this.createError = '请输入至少 3 位账户名'); if (this.userForm.password.length < 6) return (this.createError = '密码至少 6 位'); this.creatingUser = true; try { const d = await api.createUser(this.userForm); if (!d.ok || !d.api_key) throw new Error(d.message || '用户已创建，但未返回首枚密钥'); this.createdUserResult = { user: d.user, apiKey: d.api_key }; await this.loadUsers(); notify('用户和首枚密钥已创建', 'success') } catch (e) { this.createError = e.message; notify(e.message, 'error') } finally { this.creatingUser = false } }
  }
}
</script>
<style scoped>
.admin-create-user label > .app-select,.plan-group-field > .app-select { margin-top: 7px; }
.created-user-key { display:grid; gap:8px; margin:18px 0; padding:14px; border:1px solid var(--line); border-radius:11px; background:var(--paper); }
.created-user-key span { color:var(--muted); font:10px var(--mono); }
.created-user-key code { overflow-wrap:anywhere; color:var(--ink); font:11px/1.7 var(--mono); }
.created-user-actions { flex-wrap:wrap; }
:global(html[data-theme="dark"]) .created-user-key { background:#101d28; border-color:#365064; }
.daily-line-panel { grid-column: auto; }
.line-chart-wrap { min-width: 0; }
.line-chart { display: block; width: 100%; height: 190px; overflow: visible; }
.line-grid line { stroke: #e2ebf0; stroke-width: 1; stroke-dasharray: 3 4; }
.line-series { fill: none; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.line-series.tokens { stroke: #4f9fb0; }
.line-series.cost { stroke: #8a73b6; }
.line-series.activity { stroke: #5eaa8c; }
.line-point { stroke: #fff; stroke-width: 1.5; vector-effect: non-scaling-stroke; }
.line-point.tokens { fill: #4f9fb0; }
.line-point.cost { fill: #8a73b6; }
.line-point.activity { fill: #5eaa8c; }
.line-value-label { font: 8px var(--mono); text-anchor: middle; paint-order: stroke; stroke: #fff; stroke-width: 2px; stroke-linejoin: round; }
.line-value-label.tokens { fill: #367e91; }.line-value-label.cost { fill: #72589e; }.line-value-label.activity { fill: #3e8268; }
.line-axis-label { fill: #8298a9; font: 8px var(--mono); text-anchor: middle; }
.line-chart-scale { display: flex; justify-content: space-between; gap: 12px; margin-top: -1px; color: #8298a9; font: 9px var(--mono); }
.panel-period { color: #8298a9; font: 9px var(--mono); white-space: nowrap; }
.conversation-summary-panel { margin-top: 1rem; background: linear-gradient(145deg, #fff 0%, #f8fbff 72%, #f7f3fb 100%); }
.conversation-summary-panel .panel-head { margin-bottom: 20px; }
.conversation-summary-panel .panel-note { max-width: 620px; margin: 8px 0 0; }
.log-metrics { grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0 0 1rem; }
.conversation-summary-panel .metric-card { min-height: 126px; background: #ffffffd9; border-color: #dbe5ef; }
.conversation-summary-panel .metric-card:nth-child(1)::before { background: linear-gradient(90deg, #4a92aa, #6271b2); }
.conversation-summary-panel .metric-card:nth-child(2)::before { background: linear-gradient(90deg, #5eaa8c, #75a8c5); }
.conversation-summary-panel .metric-card:nth-child(3)::before { background: linear-gradient(90deg, #d18373, #c66c8a); }
.conversation-summary-panel .metric-card:nth-child(4)::before { background: linear-gradient(90deg, #8061ad, #a65f99); }
.conversation-log-list { overflow: hidden; border: 1px solid #dbe5ef; border-radius: 12px; background: #ffffffb8; }
.conversation-log-row { display: flex; justify-content: space-between; gap: 1rem; padding: 15px 17px; border-bottom: 1px solid #e7edf3; transition: background .2s; }
.conversation-log-row:last-child { border-bottom: 0; }
.conversation-log-row:hover { background: #f7fbff; }
.conversation-log-row > div { display: grid; gap: .3rem; min-width: 0; }
.conversation-log-row > div:last-child { text-align: right; }
.conversation-log-row strong { color: #24435f; font-size: 12px; }
.conversation-log-row small { color: #72859a; font-size: 11px; line-height: 1.5; }
.conversation-log-row span { display: inline-flex; justify-self: end; padding: 4px 8px; border-radius: 999px; background: #e6f6ed; color: #23805a; font: 10px var(--mono); }
.admin-overview-tabs { display: flex; align-items: center; gap: 8px; margin: 0 0 16px; padding: 6px; border: 1px solid #d7e4ee; border-radius: 14px; background: #ffffffea; box-shadow: 0 8px 20px #4163800e; position: sticky; top: 8px; z-index: 8; width: 100%; overflow-x: auto; backdrop-filter: blur(10px); }
.admin-overview-tabs button { display: inline-flex; min-height: 36px; align-items: center; padding: 0 15px; border: 0; border-radius: 9px; background: transparent; color: #54718a; cursor: pointer; font: 11px var(--mono); transition: .2s; }
.admin-overview-tabs button:hover { background: #eaf4fa; color: #1f5575; }
.admin-overview-tabs button.active { background: linear-gradient(100deg, #176f88, #5964a7); color: #fff; box-shadow: 0 6px 14px #41638026; }
.admin-runtime-metrics { grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 16px 0 22px; }
.runtime-card { min-height: 126px; }
.runtime-card strong { font-size: 27px; }
.runtime-card strong em { font: 11px var(--mono); color: #71899c; margin-left: 4px; font-style: normal; }
.runtime-card.warning::before { background: linear-gradient(90deg, #d39b55, #bc6a87); }
.runtime-card.danger::before { background: linear-gradient(90deg, #bf6477, #8c5b9d); }
.admin-visual-section { scroll-margin-top: 70px; }
.admin-section-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; margin: 34px 0 14px; }
.admin-section-heading h2 { margin: 5px 0 0; color: #1f3652; font-size: 28px; }
.admin-section-heading p { margin: 0; color: #70869a; font-size: 11px; }
.admin-dashboard-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; align-items: stretch; }
.chart-panel { min-height: 300px; }
.chart-wide { min-height: 350px; }
.chart-legend { display: flex; gap: 14px; color: #71869b; font: 10px var(--mono); }
.chart-legend span { display: inline-flex; align-items: center; gap: 5px; }
.chart-legend i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.legend-requests { background: #4e9eb0; }.legend-tokens { background: #7d6bb5; }.legend-activity { background: #5eaa8c; }
.daily-chart { display: flex; align-items: end; gap: clamp(5px, 1.2vw, 14px); height: 245px; padding: 24px 8px 0; border-bottom: 1px solid #dbe7ef; }
.daily-column { flex: 1; min-width: 22px; height: 100%; display: flex; align-items: center; flex-direction: column; justify-content: end; gap: 5px; }
.daily-bars { display: flex; align-items: end; justify-content: center; gap: 3px; height: 185px; width: 100%; }
.daily-bar-item { display: flex; align-items: center; justify-content: flex-end; flex-direction: column; gap: 3px; width: min(32px, 46%); height: 100%; }
.daily-bar-item b { max-width: 48px; overflow: hidden; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.daily-bar-item .daily-bar { width: min(18px, 72%); }
.daily-bar { display: block; width: min(18px, 42%); min-height: 4px; border-radius: 5px 5px 2px 2px; transition: height .3s ease; }
.daily-bar.requests { background: linear-gradient(180deg, #5ab5bd, #3b849c); }.daily-bar.tokens { background: linear-gradient(180deg, #9b86cd, #6b5da7); }
.daily-column b { color: #42617b; font: 10px var(--mono); }.daily-column small { color: #8ba0b0; font: 9px var(--mono); }
.rank-list { display: grid; gap: 14px; }
.rank-row { position: relative; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px 12px; align-items: center; }
.rank-row > div { min-width: 0; display: grid; gap: 4px; }.rank-row strong { overflow: hidden; color: #365b78; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.rank-row small { color: #8aa0b0; font: 10px var(--mono); }.rank-row > b { color: #557895; font: 10px var(--mono); white-space: nowrap; }.rank-track { grid-column: 1 / -1; height: 7px; overflow: hidden; border-radius: 99px; background: #e8f0f5; }.rank-track em { display: block; height: 100%; min-width: 4%; border-radius: inherit; background: linear-gradient(90deg, #51a9b3, #7468b1); transition: width .3s ease; }.rank-number { display: inline-grid; place-items: center; width: 20px; height: 20px; margin-right: 7px; border-radius: 6px; background: #edf5f7; color: #4d8090; font: 10px var(--mono); }
.split-stat-block { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }.split-stat-block h3 { margin: 0 0 12px; color: #678199; font: 10px var(--mono); letter-spacing: .08em; }.mini-stat { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 3px 8px; padding: 9px 0; border-bottom: 1px solid #e7eef3; }.mini-stat span { color: #496b84; font-size: 11px; }.mini-stat b { color: #315d7c; font: 11px var(--mono); }.mini-stat small { grid-column: 1 / -1; color: #8ba0af; font: 9px var(--mono); }
.admin-log-panel,.device-panel { margin-top: 18px; scroll-margin-top: 70px; }.log-health { display: inline-flex; align-items: center; gap: 7px; color: #2f8a67; font: 10px var(--mono); }.health-dot { width: 8px; height: 8px; border-radius: 50%; background: #55bb8b; box-shadow: 0 0 0 4px #55bb8b22; }.log-metric-strip { display: flex; flex-wrap: wrap; gap: 9px; margin: -2px 0 17px; }.log-metric-strip span { padding: 8px 11px; border: 1px solid #dbe7ef; border-radius: 9px; background: #f7fbfd; color: #6c8498; font: 10px var(--mono); }.log-metric-strip b { color: #315b79; margin-left: 5px; }.runtime-log-list { overflow: hidden; border: 1px solid #dbe6ee; border-radius: 12px; background: #ffffffb8; }.runtime-log-row { display: grid; grid-template-columns: 48px minmax(0, 1fr); align-items: start; gap: 12px; padding: 13px 15px; border-bottom: 1px solid #e7eef3; }.runtime-log-row:last-child { border-bottom: 0; }.runtime-log-row > div { min-width: 0; display: grid; gap: 5px; }.runtime-log-row strong { overflow: hidden; color: #3c5c74; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }.runtime-log-row small { color: #8297a7; font: 10px var(--mono); overflow-wrap: anywhere; }.log-level { display: inline-flex; justify-content: center; padding: 5px 4px; border-radius: 6px; font: 9px var(--mono); }.log-level.warn { background: #fff1dc; color: #a76a26; }.log-level.error { background: #ffe4e7; color: #ad4f62; }
.device-updated { color: #8298a9; font: 10px var(--mono); }.device-meta { display: flex; flex-wrap: wrap; gap: 8px; margin: -3px 0 20px; }.device-meta span { padding: 7px 10px; border-radius: 8px; background: #f0f6fa; color: #678299; font: 10px var(--mono); }.device-metrics-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }.device-meter { padding: 17px; border: 1px solid #dbe7ef; border-radius: 12px; background: #fbfdff; }.meter-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; color: #58768d; font-size: 11px; }.meter-head b { color: #315d7c; font: 13px var(--mono); }.meter-track { height: 9px; margin: 13px 0 9px; overflow: hidden; border-radius: 99px; background: #e6eef4; }.meter-track i { display: block; height: 100%; min-width: 3px; border-radius: inherit; transition: width .3s ease; }.meter-track i.cpu { background: linear-gradient(90deg, #4caab4, #6178bd); }.meter-track i.memory { background: linear-gradient(90deg, #6e9bc4, #8567b0); }.meter-track i.storage { background: linear-gradient(90deg, #d09b60, #ba687b); }.device-meter > small { color: #8ba0ae; font: 10px var(--mono); }.network-meter { background: linear-gradient(145deg, #f8fcff, #f8f6ff); }.network-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; margin-top: 16px; color: #71899b; font: 10px var(--mono); }.network-stats span { padding-top: 8px; border-top: 1px solid #dfeaf1; }
@media (max-width: 1180px) { .admin-dashboard-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 1050px) { .admin-runtime-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }.chart-wide { min-height: 320px; } }
@media (max-width: 760px) {
  .admin-overview-tabs { width: 100%; overflow-x: auto; justify-content: flex-start; }.admin-overview-tabs button { white-space: nowrap; }.admin-section-heading { align-items: flex-start; flex-direction: column; gap: 8px; }.admin-dashboard-grid { grid-template-columns: 1fr; }.device-metrics-grid { grid-template-columns: 1fr; }.split-stat-block { grid-template-columns: 1fr; gap: 16px; }
  .daily-chart { gap: 3px; }.daily-bar { width: 40%; }.daily-column small { font-size: 11px; }
  .log-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .conversation-log-row { align-items: flex-start; flex-direction: column; }
  .conversation-log-row > div:last-child { text-align: left; }
  .conversation-log-row span { justify-self: start; }
}

/* The visual dashboard follows the compact typography used by the other admin panels. */
.admin-visual-section .admin-section-heading{margin:18px 0 8px}
.admin-visual-section .admin-section-heading .eyebrow{display:block;font:11px var(--mono);letter-spacing:.13em;color:#7890a2}
.admin-visual-section .admin-section-heading h2{margin:5px 0 0;color:#1f3652;font:500 20px/1.22 'Playfair Display',Georgia,serif;letter-spacing:-.02em}
.admin-visual-section .admin-section-heading p{font-size:12px;line-height:1.5}
.admin-visual-section .admin-dashboard-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.admin-visual-section .chart-panel{min-height:220px;padding:15px}
.admin-visual-section .chart-wide{min-height:250px}
.admin-visual-section .chart-panel .panel-head{margin-bottom:8px}
.admin-visual-section .chart-panel .panel-head h2{font-size:14px;line-height:1.2}
.admin-visual-section .chart-legend{font-size:11px;gap:8px}
.admin-visual-section .daily-chart{height:176px;padding-top:10px}
.admin-visual-section .daily-bars{height:130px}
.admin-visual-section .daily-column{gap:2px}
.admin-visual-section .daily-bar-item b{font-size:8px}
.admin-visual-section .daily-column small{font-size:11px}
.admin-visual-section .rank-list{gap:8px}
.admin-visual-section .rank-row{gap:3px 8px}
.admin-visual-section .rank-row strong{font-size:12px}
.admin-visual-section .rank-row small,.admin-visual-section .rank-row>b{font-size:11px}
.admin-visual-section .rank-track{height:5px}
.admin-visual-section .rank-number{width:22px;height:22px;margin-right:5px;font-size:11px}
.admin-visual-section .split-stat-block{gap:10px}
.admin-visual-section .split-stat-block h3{margin-bottom:6px;font-size:12px}
.admin-visual-section .mini-stat{padding:6px 0}
.admin-visual-section .mini-stat span{font-size:12px}
.admin-visual-section .mini-stat b{font-size:12px}
.admin-visual-section .mini-stat small{font-size:11px}
.admin-visual-section .combined-rank-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.admin-visual-section .combined-rank-grid>section{min-width:0}
.admin-visual-section .combined-rank-grid h3{margin:0 0 9px;color:#637f94;font:10px var(--mono);letter-spacing:.06em}
.admin-visual-section .combined-rank-grid .rank-list{max-height:250px;overflow-y:auto;padding-right:4px}
.admin-visual-section .user-activity-panel{min-height:210px}
.admin-visual-section .user-activity-panel .line-chart{height:140px}
.admin-visual-section .visual-model-usage{grid-column:auto;min-height:0}
.admin-visual-section .visual-model-usage .model-usage-chart{margin-top:2px;overflow-x:hidden;padding-right:0}
.admin-visual-section .visual-model-usage .model-usage-plot{gap:8px;min-width:0;min-height:190px;padding-left:6px;padding-right:6px}
.admin-visual-section .visual-model-usage .model-usage-group{flex:1 1 0;min-width:0;min-height:176px;padding-left:1px;padding-right:1px}
.admin-visual-section .visual-model-usage .model-usage-bars{height:110px}
:global(html[data-theme="dark"]) .line-value-label{stroke:#152632}
@media(max-width:760px){.admin-visual-section .admin-dashboard-grid,.admin-visual-section .combined-rank-grid{grid-template-columns:1fr}}
.admin-log-conversations{margin-top:24px;padding-top:24px;border-top:1px solid #dbe5ef}
.admin-log-conversations.conversation-summary-panel{margin-top:24px;background:transparent;border-left:0;border-right:0;border-bottom:0;border-radius:0}
.admin-log-conversations .panel-head{margin-bottom:14px}
.admin-log-conversations .metric-grid{margin-bottom:16px}
.admin-log-export{margin-top:24px;padding-top:24px;border-top:1px solid #dbe5ef}
.admin-log-export .panel-head{align-items:flex-end;margin-bottom:14px}
.admin-log-export .panel-head .panel-note{max-width:680px;margin-top:8px}
.admin-log-export .code-block{margin-top:0}
.admin-log-export .export-note{margin:12px 0 0}
.export-controls{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}.export-controls label{display:grid;gap:6px;color:#708595;font-size:11px}.export-controls input,.export-controls select{width:100%;height:40px;padding:0 10px;border:1px solid #cfdee9;border-radius:9px;background:#fff;color:inherit;font:11px var(--mono)}
@media(max-width:760px){
  .admin-log-export .panel-head{align-items:flex-start;flex-direction:column;gap:12px}
  .admin-log-export .panel-head .secondary-btn{width:100%}
  .export-controls{grid-template-columns:1fr}
}
</style>
