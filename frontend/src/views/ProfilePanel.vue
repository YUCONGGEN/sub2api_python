<template>
  <section class="page">
    <div class="page-head"><div><div class="eyebrow">ACCOUNT / SETTINGS</div><h1>个人资料</h1><p>管理账户资料、密码和登录设备。</p></div></div>
    <div class="profile-layout">
      <div class="panel profile-hero"><div class="profile-avatar">{{ initials }}</div><h2>{{ user.username }}</h2><p>{{ user.role === 'ADMIN' ? '管理员账户' : '标准用户' }}</p><div v-if="user.group_name" class="profile-group-level"><span>当前分组</span><strong>{{ user.group_name }}</strong><b>权重 {{ Number(user.group_weight || 0) }} · 并发 {{ Number(user.group_concurrency_limit || 1) }}</b><small>{{ user.group_source === 'SUBSCRIPTION' ? `套餐生效${user.group_source_plan_name ? ` · ${user.group_source_plan_name}` : ''}` : '基础分组' }}</small></div><div class="profile-meta"><span>账户 ID <b>#{{ user.id }}</b></span><span>注册于 <b>{{ format(user.created_at) }}</b></span></div></div>
      <div class="panel"><div class="panel-head"><div><span class="eyebrow">ACCOUNT BINDING</span><h2>账户信息</h2></div></div><form @submit.prevent="saveProfile"><label>邮箱<input v-model.trim="email" type="email" placeholder="可选；当前系统不发送邮件通知" /></label><p class="panel-note">邮箱仅用于账户资料标识，启用邮件通知前不会向此地址发送消息。</p><button class="primary-btn" :disabled="saving">{{ saving ? '保存中…' : '保存资料' }}</button><p v-if="message" :class="profileError ? 'form-error' : 'form-success'">{{ message }}</p></form></div>
      <div class="panel"><div class="panel-head"><div><span class="eyebrow">SECURITY</span><h2>修改密码</h2></div></div><form @submit.prevent="changePassword"><label>当前密码<input v-model="password.current_password" type="password" autocomplete="current-password" required /></label><label>新密码<input v-model="password.new_password" type="password" autocomplete="new-password" minlength="6" required /></label><label>确认新密码<input v-model="password.confirm" type="password" autocomplete="new-password" required /></label><button class="secondary-btn" :disabled="savingPassword">{{ savingPassword ? '更新中…' : '更新密码并退出所有设备' }}</button><p v-if="passwordError" class="form-error">{{ passwordError }}</p></form></div>
      <div class="panel preference-panel"><div><span class="eyebrow">QUICK LINKS</span><h2>账户工具</h2></div><div class="profile-links"><router-link to="/keys" class="secondary-btn">管理 API 密钥 ↗</router-link><router-link to="/billing" class="secondary-btn">前往钱包 ↗</router-link><router-link to="/docs" class="secondary-btn">查看接入教程 ↗</router-link></div></div>
      <div class="panel sessions-panel">
        <div class="panel-head"><div><span class="eyebrow">LOGIN DEVICES</span><h2>登录设备</h2></div><button v-if="sessionPagination.total > 1" class="secondary-btn" :disabled="sessionBusy" @click="revokeOthers">退出其他设备</button></div>
        <div v-if="sessionError" class="data-error" role="alert"><span>{{ sessionError }}</span><button class="secondary-btn" @click="loadSessions">重试</button></div>
        <div v-if="sessionLoading" class="loading-state">正在读取登录设备…</div>
        <div v-else-if="legacySession" class="session-legacy"><strong>当前是旧版登录会话</strong><span>无需立即退出；下次正常登录后即可查看和管理设备。</span></div>
        <div v-else-if="sessions.length" class="session-list">
          <article v-for="session in sessions" :key="session.id"><div><strong>{{ deviceName(session.user_agent) }} <span v-if="session.current" class="status success">当前设备</span></strong><small>{{ session.ip_address || 'IP 未记录' }} · 最近活动 {{ formatTime(session.last_seen_at) }}</small><small>登录于 {{ formatTime(session.created_at) }}</small></div><button class="text-btn danger" :disabled="sessionBusy" @click="revoke(session)">{{ session.current ? '退出当前设备' : '退出此设备' }}</button></article>
        </div>
        <div v-else-if="!sessionError" class="empty compact-empty">暂无设备记录</div>
        <div v-if="!legacySession && sessionPagination.pages > 1" class="pagination"><button class="secondary-btn" :disabled="sessionLoading || sessionBusy || sessionPagination.page <= 1" @click="changeSessionPage(sessionPagination.page - 1)">上一页</button><span>第 {{ sessionPagination.page }} / {{ sessionPagination.pages }} 页，共 {{ sessionPagination.total }} 个设备</span><button class="secondary-btn" :disabled="sessionLoading || sessionBusy || sessionPagination.page >= sessionPagination.pages" @click="changeSessionPage(sessionPagination.page + 1)">下一页</button></div>
      </div>
    </div>
  </section>
</template>

<script>
import { api, clearAuthCache } from '../api'
import { askConfirm, notify } from '../ui'

export default {
  props: { user: Object },
  data: () => ({ email: '', saving: false, message: '', profileError: false, password: { current_password: '', new_password: '', confirm: '' }, savingPassword: false, passwordError: '', sessions: [], legacySession: false, sessionLoading: false, sessionBusy: false, sessionError: '', sessionPagination: { page: 1, page_size: 5, total: 0, pages: 1 } }),
  computed: { initials () { return this.user && this.user.username ? this.user.username.slice(0, 1).toUpperCase() : '' } },
  created () { this.email = (this.user && this.user.email) || ''; this.loadSessions() },
  methods: {
    async saveProfile () { this.saving = true; this.message = ''; this.profileError = false; try { await api.updateProfile({ email: this.email }); this.message = '资料已保存'; this.$emit('refresh-user') } catch (e) { this.profileError = true; this.message = e.message } finally { this.saving = false } },
    async changePassword () { this.passwordError = ''; if (this.password.new_password !== this.password.confirm) return (this.passwordError = '两次新密码不一致'); this.savingPassword = true; try { await api.changePassword(this.password); notify('密码已更新，所有旧会话已失效，请重新登录', 'success'); localStorage.removeItem('rose_token'); clearAuthCache(); this.$router.push('/login') } catch (e) { this.passwordError = e.message } finally { this.savingPassword = false } },
    async loadSessions (page = this.sessionPagination.page) { this.sessionLoading = true; this.sessionError = ''; try { const requestedPage = Math.max(1, Number(page || 1)); const data = await api.sessions({ page: requestedPage, page_size: this.sessionPagination.page_size }); const rows = data.sessions || []; this.legacySession = !!data.legacy_session; if (data.pagination) { this.sessions = rows; this.sessionPagination = data.pagination } else { const pageSize = this.sessionPagination.page_size; const total = rows.length; const pages = Math.max(1, Math.ceil(total / pageSize)); const safePage = Math.min(requestedPage, pages); this.sessions = rows.slice((safePage - 1) * pageSize, safePage * pageSize); this.sessionPagination = { page: safePage, page_size: pageSize, total, pages } } } catch (e) { this.sessionError = e.message || '设备列表加载失败' } finally { this.sessionLoading = false } },
    changeSessionPage (page) { this.loadSessions(page) },
    async revokeOthers () { if (!await askConfirm('确定退出除当前设备外的所有登录会话吗？')) return; this.sessionBusy = true; try { const data = await api.revokeOtherSessions(); notify(`已退出 ${data.revoked || 0} 个其他设备`, 'success'); await this.loadSessions(1) } catch (e) { notify(e.message, 'error') } finally { this.sessionBusy = false } },
    async revoke (session) { if (!await askConfirm(`确定退出${session.current ? '当前' : '此'}设备吗？`)) return; this.sessionBusy = true; try { await api.revokeSession(session.id); if (session.current) { localStorage.removeItem('rose_token'); clearAuthCache(); return this.$router.push('/login') } await this.loadSessions(this.sessionPagination.page); notify('设备已退出', 'success') } catch (e) { notify(e.message, 'error') } finally { this.sessionBusy = false } },
    deviceName (userAgent) { const value = String(userAgent || '未知设备'); if (/Windows/i.test(value)) return 'Windows 设备'; if (/Macintosh|Mac OS/i.test(value)) return 'Mac 设备'; if (/iPhone|iPad/i.test(value)) return 'iPhone / iPad'; if (/Android/i.test(value)) return 'Android 设备'; return value.slice(0, 48) },
    format (value) { return value ? new Date(value).toLocaleDateString('zh-CN') : '-' },
    formatTime (value) { return value ? new Date(value).toLocaleString('zh-CN') : '-' }
  }
}
</script>
