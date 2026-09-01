<template>
    <div class="app-shell" :class="[{ 'login-shell': $route.meta.public, 'theme-dark': theme === 'dark' }, themeClass]">
    <FeedbackModal />
    <template v-if="!$route.meta.public">
      <aside class="sidebar">
        <div class="brand" @click="$router.push('/dashboard')"><span>{{ siteName }}</span></div>
        <div class="workspace-label">AI GATEWAY <span>LIVE</span></div>
        <nav>
          <router-link to="/dashboard"><span class="nav-index">01</span>控制台</router-link>
          <router-link to="/models"><span class="nav-index">02</span>模型广场</router-link>
          <router-link to="/billing"><span class="nav-index">03</span>余额与充值</router-link>
          <router-link to="/docs"><span class="nav-index">04</span>配置教程</router-link>
          <router-link to="/monitoring"><span class="nav-index">05</span>模型监控</router-link>
          <router-link to="/keys"><span class="nav-index">06</span>API 密钥</router-link>
          <router-link to="/profile"><span class="nav-index">07</span>个人资料</router-link>
          <router-link v-if="user && user.role === 'ADMIN'" to="/admin"><span class="nav-index">08</span>管理后台</router-link>
          <router-link v-if="user && user.role === 'ADMIN'" to="/admin/upstream-subscriptions"><span class="nav-index">09</span>订阅账号</router-link>
        </nav>
        <div class="sidebar-bottom">
          <div class="status-dot"><i></i>所有系统正常</div>
          <div v-if="user" class="user-mini" @click="logout"><div class="avatar">{{ initials }}</div><div><strong>{{ user.username }}</strong><small>退出登录</small></div><span class="arrow">↗</span></div>
        </div>
      </aside>
      <main class="main-content">
        <header class="topbar"><div class="crumb"><span>{{ siteName }}</span><b>/</b>{{ pageTitle }}</div><div class="top-actions"><button class="theme-switch" type="button" @click="toggleTheme">主题：{{ theme === 'dark' ? '夜晚' : '白天' }}</button><router-link to="/docs">新手指南 ↗</router-link></div></header>
        <router-view :user="user" :app-name="siteName" :api-base-url="apiBaseUrl" :recharge-code-placeholder="rechargeCodePlaceholder" :codex-config="codexConfig" :model-options="modelOptions" @refresh-user="refreshUser" />
      </main>
    </template>
    <router-view v-else :app-name="siteName" :api-base-url="apiBaseUrl" :recharge-code-placeholder="rechargeCodePlaceholder" :codex-config="codexConfig" :model-options="modelOptions" />
  </div>
</template>

<script>
import { api, clearAuthCache } from './api'

 export default { name: 'App', data: () => ({ user: null, siteName: '', apiBaseUrl: '', rechargeCodePlaceholder: '', codexConfig: null, modelOptions: [], theme: 'light' }), computed: { pageTitle () { return ({ '/dashboard': '控制台', '/models': '模型广场', '/billing': '余额与充值', '/admin': '管理后台', '/admin/upstream-subscriptions': '订阅账号', '/docs': '配置教程', '/monitoring': '模型监控', '/keys': 'API 密钥', '/profile': '个人资料', '/conversations': '会话记录' })[this.$route.path] || '' }, initials () { return this.user?.username ? this.user.username.slice(0, 1).toUpperCase() : '' }, themeClass () { return this.theme === 'dark' ? 'theme-dark' : 'theme-light' } }, watch: { '$route.path' () { this.loadUser(); this.updateTitle() } }, created () { this.loadConfig(); this.loadUser(); this.loadTheme(); this.updateTitle(); window.addEventListener('rose:auth-expired', this.handleAuthExpired) }, beforeDestroy () { window.removeEventListener('rose:auth-expired', this.handleAuthExpired) }, methods: { async loadConfig () { try { const data = await api.publicConfig(); if (data.name) this.siteName = data.name; if (data.api_base_url) this.apiBaseUrl = String(data.api_base_url).replace(/\/$/, ''); if (data.recharge_code_placeholder) this.rechargeCodePlaceholder = data.recharge_code_placeholder; if (data.codex) this.codexConfig = data.codex; if (Array.isArray(data.models)) this.modelOptions = data.models; this.updateTitle() } catch (e) {} }, loadTheme () { const saved = localStorage.getItem('rose_theme'); if (saved === 'dark' || saved === 'light') this.theme = saved; this.applyTheme() }, applyTheme () { if (typeof document === 'undefined') return; document.documentElement.setAttribute('data-theme', this.theme); localStorage.setItem('rose_theme', this.theme) }, toggleTheme () { this.theme = this.theme === 'dark' ? 'light' : 'dark'; this.applyTheme() }, updateTitle () { if (typeof document !== 'undefined') document.title = this.siteName ? (this.pageTitle ? `${this.pageTitle} - ${this.siteName}` : this.siteName) : '' }, async loadUser (force = false) { if (!localStorage.getItem('rose_token')) { this.user = null; return }; try { const data = await api.me({ force }); this.user = data.user } catch (e) { this.logout() } }, refreshUser () { this.loadUser(true) }, handleAuthExpired () { this.user = null; if (this.$route.path !== '/login') this.$router.push('/login').catch(() => {}) }, logout () { localStorage.removeItem('rose_token'); clearAuthCache(); this.user = null; if (this.$route.path !== '/login') this.$router.push('/login').catch(() => {}) } } }
</script>
