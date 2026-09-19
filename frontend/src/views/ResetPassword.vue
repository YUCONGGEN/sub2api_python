<template>
  <div class="login-page register-page codex-auth">
    <div class="codex-auth-orb orb-one" aria-hidden="true"></div><div class="codex-auth-orb orb-two" aria-hidden="true"></div>
    <div class="codex-auth-snow" aria-hidden="true"><span></span><span></span><span></span></div>
    <header class="codex-auth-header"><div class="codex-auth-brand"><span class="codex-auth-mark" aria-hidden="true">↗</span><span>{{ appName }}</span></div><router-link class="codex-auth-header-action" to="/login">返回登录</router-link></header>
    <main class="codex-auth-main">
      <section class="codex-auth-hero"><div class="codex-auth-kicker"><i></i> ACCOUNT SECURITY</div><p>设置新密码后，所有已登录设备会立即退出，重置链接也会同步失效。</p><div class="codex-auth-features"><span>短时链接</span><span>一次有效</span><span>退出全部设备</span></div></section>
      <section class="codex-auth-panel" aria-labelledby="reset-title">
        <div class="codex-auth-panel-head"><span>PASSWORD RESET</span><h2 id="reset-title">设置新密码</h2><p>新密码长度为 6-128 位。</p></div>
        <form v-if="!success" @submit.prevent="submit">
          <label>新密码<input v-model="form.password" type="password" minlength="6" maxlength="128" autocomplete="new-password" placeholder="输入新密码" /></label>
          <label>确认新密码<input v-model="form.confirm" type="password" minlength="6" maxlength="128" autocomplete="new-password" placeholder="再次输入新密码" /></label>
          <button class="codex-auth-submit" :disabled="loading || !canSubmit">{{ loading ? '正在重置…' : '重置密码' }}<b aria-hidden="true">→</b></button>
          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        </form>
        <div v-else class="recovery-result" role="status"><div class="recovery-result-mark">✓</div><h3>密码已重置</h3><p>请使用新密码重新登录。</p></div>
        <div class="register-line"><router-link to="/login">返回登录</router-link></div>
      </section>
    </main>
    <footer class="codex-auth-footer"><span>{{ appName }}</span><span>AI GATEWAY · 2026</span></footer>
  </div>
</template>
<script>
import { api } from '../api'
export default {
  props: { appName: String },
  data: () => ({ form: { password: '', confirm: '' }, loading: false, error: '', success: false }),
  computed: { canSubmit () { return this.form.password.length >= 6 && this.form.password === this.form.confirm && !!this.$route.query.token } },
  created () { if (!this.$route.query.token) this.error = '重置链接缺少令牌，请重新申请。' },
  methods: { async submit () { if (this.form.password !== this.form.confirm) return (this.error = '两次输入的密码不一致'); this.loading = true; this.error = ''; try { await api.resetPassword({ token: this.$route.query.token, new_password: this.form.password }); this.success = true } catch (e) { this.error = e.message } finally { this.loading = false } } }
}
</script>
