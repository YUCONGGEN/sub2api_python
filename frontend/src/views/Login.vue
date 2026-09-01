<template>
  <div class="login-page"><div class="login-grid"></div><div class="login-left"><div class="brand"><span>{{ appName }}</span></div><div class="login-hero"><div class="eyebrow">OPENAI COMPATIBLE GATEWAY</div><h1>把模型能力<br /><em>接入你的工作流。</em></h1><p>统一的 API、透明的计费，以及一套安静可靠的开发者控制台。</p><div class="login-stat"><span><b>安全</b><small>账户登录保护</small></span><span><b>24/7</b><small>网关运行监控</small></span><span><b>1</b><small>统一模型入口</small></span></div></div><div class="login-foot">{{ appName }} / 2026</div></div><div class="login-card"><div class="mobile-brand brand"><span>{{ appName }}</span></div><div class="card-kicker">WELCOME BACK</div><h2>登录控制台</h2><p class="card-sub">使用你的 {{ appName }} 账户继续。</p><form @submit.prevent="submit"><label>账户名<input v-model.trim="form.username" autocomplete="username" placeholder="输入账户名" /></label><label>密码<input v-model="form.password" type="password" autocomplete="current-password" placeholder="输入密码" /></label><button class="primary-btn full" :disabled="loading">{{ loading ? '正在验证…' : '进入控制台 →' }}</button><p v-if="error" class="form-error">{{ error }}</p></form><div class="login-note">管理员账户由服务端 YAML/环境变量配置。</div><div class="register-line">还没有账户？<router-link to="/register">创建一个账户</router-link></div></div></div>
</template>
<script>
import { api } from '../api'
export default { props: { appName: String }, data: () => ({ form: { username: '', password: '' }, loading: false, error: '' }), methods: { async submit () { this.error = ''; this.loading = true; try { const data = await api.login(this.form); if (!data.ok) throw new Error(data.message); localStorage.setItem('rose_token', data.token); this.$router.push('/dashboard') } catch (e) { this.error = e.message } finally { this.loading = false } } } }
</script>



