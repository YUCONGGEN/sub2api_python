<template>
  <div class="login-page register-page"><div class="login-grid"></div><div class="login-left"><div class="brand"><span>{{ appName }}</span></div><div class="login-hero"><div class="eyebrow">START WITH {{ appName ? appName.toUpperCase() : '' }}</div><h1>从一个账户<br /><em>开始调用模型。</em></h1><p>创建账户后，在 API 密钥页面按需创建独立密钥，按实际 token 用量透明计费。</p><div class="login-stat"><span><b>∞</b><small>模型目录扩展</small></span><span><b>API</b><small>密钥自主创建</small></span><span><b>24/7</b><small>统一 API 入口</small></span></div></div><div class="login-foot">{{ appName }} / 2026</div></div><div class="login-card register-card"><div class="mobile-brand brand"><span>{{ appName }}</span></div><div class="card-kicker">CREATE ACCOUNT</div><h2>创建账户</h2><p class="card-sub">注册后请前往 API 密钥页面创建密钥。</p><form @submit.prevent="submit" novalidate><label>账户名<input v-model.trim="form.username" autocomplete="username" minlength="3" maxlength="32" placeholder="至少 3 位字符" /></label><label>邮箱 <span class="optional">可选</span><input v-model.trim="form.email" type="email" autocomplete="email" placeholder="仅用于账户资料，暂不发送通知" /></label><label>密码<input v-model="form.password" type="password" autocomplete="new-password" minlength="6" placeholder="至少 6 位字符" /></label><div class="password-meter"><span :class="strengthClass"></span></div><small class="password-hint">{{ strengthText }}</small><label>确认密码<input v-model="form.confirm" type="password" autocomplete="new-password" placeholder="再次输入密码" /></label><label class="check-line"><input v-model="form.agree" type="checkbox" /> <span>我同意遵守服务条款并合理使用 API</span></label><button class="primary-btn full" :disabled="loading || !canSubmit">{{ loading ? '正在创建…' : '创建账户 →' }}</button><p v-if="error" class="form-error">{{ error }}</p></form><div class="register-line">已经有账户？<router-link to="/login">返回登录</router-link></div></div></div>
</template>
<script>
import { api } from '../api'
import { notify } from '../ui'
export default {
  props: { appName: String },
  data: () => ({ form: { username: '', email: '', password: '', confirm: '', agree: false }, loading: false, error: '', errorTimer: null }),
  computed: {
    strength () { const value = this.form.password || ''; return (value.length >= 10 ? 1 : 0) + (/[A-Z]/.test(value) ? 1 : 0) + (/[a-z]/.test(value) ? 1 : 0) + (/[0-9]/.test(value) ? 1 : 0) + (/[^A-Za-z0-9]/.test(value) ? 1 : 0) },
    strengthClass () { return this.strength >= 4 ? 'strong' : this.strength >= 2 ? 'medium' : 'weak' },
    strengthText () { return !this.form.password ? '密码至少 6 位' : this.strength >= 4 ? '密码强度较高' : this.strength >= 2 ? '密码强度一般' : '密码强度较弱' },
    canSubmit () { return this.form.username.length >= 3 && this.form.password.length >= 6 && this.form.password === this.form.confirm && this.form.agree }
  },
  methods: {
    showError (message) {
      this.error = message
      if (this.errorTimer) clearTimeout(this.errorTimer)
      this.errorTimer = setTimeout(() => { this.error = '' }, 3000)
      notify(message.includes('账户已存在') || message.includes('已经注册') || message.includes('更换账户名') ? '该用户已经注册，请更换账户名' : message, 'error')
    },
    async submit () {
      this.error = ''
      if (!this.canSubmit) {
        this.showError(this.form.password !== this.form.confirm ? '两次输入的密码不一致' : '请完整填写注册信息并勾选协议')
        return
      }
      this.loading = true
      try {
        const data = await api.register({ username: this.form.username, password: this.form.password, email: this.form.email })
        if (!data.ok) throw new Error(data.message || '注册失败，请稍后重试')
        localStorage.setItem('rose_token', data.token)
        this.$router.push('/dashboard')
      } catch (e) {
        this.showError(e.message || '注册失败，请稍后重试')
      } finally {
        this.loading = false
      }
    }
  },
  beforeDestroy () {
    if (this.errorTimer) clearTimeout(this.errorTimer)
  }
}
</script>



