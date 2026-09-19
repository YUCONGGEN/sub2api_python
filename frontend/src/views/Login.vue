<template>
  <div class="login-page codex-auth codex-auth-with-links">
    <div class="codex-auth-orb orb-one" aria-hidden="true"></div>
    <div class="codex-auth-orb orb-two" aria-hidden="true"></div>
    <div class="codex-auth-snow" aria-hidden="true"><span></span><span></span><span></span></div>

    <header class="codex-auth-header">
      <div class="codex-auth-brand-cluster">
        <div class="codex-auth-brand">
          <span class="codex-auth-mark" aria-hidden="true">↗</span>
          <span>{{ appName }}</span>
        </div>
        <nav class="codex-auth-links" aria-label="教程与应用入口">
          <router-link class="codex-auth-docs-link" to="/docs"><i aria-hidden="true">⌘</i><span>配置教程</span><b aria-hidden="true">↗</b></router-link>
          <a class="codex-auth-docs-link" href="http://www.yucg.cn:8235" target="_blank" rel="noopener noreferrer" title="YuDesk远程桌面（新窗口打开）">
            <i aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8m-4-4v4"/></svg></i><span>YuDesk远程桌面</span><b aria-hidden="true">↗</b>
          </a>
          <a class="codex-auth-docs-link" href="http://www.yucg.cn:8250" target="_blank" rel="noopener noreferrer" title="WeLink即时办公（新窗口打开）">
            <i aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11.5a8 8 0 0 1-8 8H4l1.7-3.4A8 8 0 1 1 20 11.5Z"/><path d="M8 10h8m-8 4h5"/></svg></i><span>WeLink即时办公</span><b aria-hidden="true">↗</b>
          </a>
        </nav>
      </div>
      <router-link class="codex-auth-header-action" to="/register">注册账户</router-link>
    </header>

    <main class="codex-auth-main">
      <section class="codex-auth-hero">
        <div class="codex-auth-kicker"><i></i> OPENAI COMPATIBLE GATEWAY</div>
        <p>统一的 API、透明的计费和清晰的运行状态，让模型能力自然进入你的工作流。</p>
        <div class="codex-auth-features" aria-label="平台特性">
          <span>统一模型入口</span>
          <span>实时用量统计</span>
          <span>安全密钥管理</span>
        </div>
      </section>

      <section class="codex-auth-panel" aria-labelledby="login-title">
        <div class="codex-auth-panel-head">
          <span>{{ recoveryOpen ? 'ACCOUNT RECOVERY' : 'WELCOME BACK' }}</span>
          <h2 id="login-title">{{ recoveryOpen ? '找回登录密码' : '登录控制台' }}</h2>
          <p>{{ recoveryOpen ? '优先使用账户绑定邮箱找回；没有邮箱时可联系管理员。' : `使用你的 ${appName} 账户继续。` }}</p>
        </div>
        <form v-if="!recoveryOpen" @submit.prevent="submit">
          <label>账户名<input v-model.trim="form.username" autocomplete="username" placeholder="输入账户名" /></label>
          <label>密码<input v-model="form.password" type="password" autocomplete="current-password" placeholder="输入密码" /></label>
          <button v-if="passwordRecoveryEnabled" type="button" class="auth-text-action" @click="openRecovery">忘记密码？</button>
          <button class="codex-auth-submit" :disabled="loading">{{ loading ? '正在验证…' : '继续' }}<b aria-hidden="true">→</b></button>
          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        </form>
        <form v-else-if="recoveryStage === 'account'" @submit.prevent="requestRecovery">
          <label>账户名<input v-model.trim="recovery.username" autocomplete="username" placeholder="输入需要找回的账户名" /></label>
          <button class="codex-auth-submit" :disabled="recoveryLoading || !recovery.username">{{ recoveryLoading ? '正在发送…' : '发送找回邮件' }}<b aria-hidden="true">→</b></button>
          <p v-if="recoveryError" class="form-error" role="alert">{{ recoveryError }}</p>
        </form>
        <form v-else-if="recoveryStage === 'contact'" @submit.prevent="contactAdmin">
          <p class="recovery-notice">该账号未填写邮箱，请联系管理员找回密码。</p>
          <label>账户名<input :value="recovery.username" disabled /></label>
          <label>姓名<input v-model.trim="recovery.name" maxlength="64" placeholder="请填写真实姓名（必填）" /></label>
          <label>公司或学校 <span class="optional">可选</span><input v-model.trim="recovery.organization" maxlength="128" placeholder="用于管理员核实身份" /></label>
          <button class="codex-auth-submit" :disabled="recoveryLoading || recovery.name.length < 2">{{ recoveryLoading ? '正在发送…' : '发送给管理员' }}<b aria-hidden="true">→</b></button>
          <p v-if="recoveryError" class="form-error" role="alert">{{ recoveryError }}</p>
        </form>
        <div v-else class="recovery-result" role="status">
          <div class="recovery-result-mark">✓</div>
          <h3>{{ recoveryStage === 'email-sent' ? '邮件已发送' : '申请已提交' }}</h3>
          <p>{{ recoveryMessage }}</p>
          <p v-if="maskedEmail" class="recovery-email">发送至 {{ maskedEmail }}</p>
        </div>
        <template v-if="!recoveryOpen">
          <div class="login-note">管理员账户由服务端 YAML/环境变量配置。</div>
          <div class="register-line">还没有账户？<router-link to="/register">创建一个账户</router-link></div>
        </template>
        <div v-else class="register-line"><button type="button" class="auth-back-action" @click="closeRecovery">← 返回登录</button></div>
      </section>
    </main>

    <footer class="codex-auth-footer"><div class="codex-auth-footer-left"><span>{{ appName }}</span><a href="https://github.com/YUCONGGEN/sub2api_python" target="_blank" rel="noopener"><i aria-hidden="true">GH</i><span>GitHub</span><b aria-hidden="true">↗</b></a></div><span>AI GATEWAY · 2026</span></footer>
  </div>
</template>
<script>
import { api } from '../api'
export default {
  props: { appName: String, passwordRecoveryEnabled: { type: Boolean, default: true } },
  data: () => ({
    form: { username: '', password: '' }, loading: false, error: '', recoveryOpen: false,
    recoveryStage: 'account', recoveryLoading: false, recoveryError: '', recoveryMessage: '', maskedEmail: '',
    recovery: { username: '', name: '', organization: '' }
  }),
  methods: {
    async submit () { this.error = ''; this.loading = true; try { const data = await api.login(this.form); if (!data.ok) throw new Error(data.message); window.sessionStorage.removeItem('rose_fresh_api_key'); localStorage.setItem('rose_token', data.token); this.$router.push('/dashboard') } catch (e) { this.error = e.message } finally { this.loading = false } },
    openRecovery () { this.recoveryOpen = true; this.recoveryStage = 'account'; this.recoveryError = ''; this.recovery.username = this.form.username },
    closeRecovery () { this.recoveryOpen = false; this.recoveryStage = 'account'; this.recoveryError = ''; this.recoveryMessage = ''; this.maskedEmail = '' },
    async requestRecovery () { this.recoveryLoading = true; this.recoveryError = ''; try { const data = await api.requestPasswordRecovery({ username: this.recovery.username }); if (data.channel === 'contact_admin') { this.recoveryStage = 'contact'; this.recoveryMessage = data.message || '' } else { this.recoveryStage = 'email-sent'; this.maskedEmail = data.masked_email || ''; this.recoveryMessage = data.message || '找回邮件已发送，通常会在 1 分钟左右到达，请耐心等待。' } } catch (e) { this.recoveryError = e.message } finally { this.recoveryLoading = false } },
    async contactAdmin () { this.recoveryLoading = true; this.recoveryError = ''; try { const data = await api.contactPasswordRecovery(this.recovery); this.recoveryStage = 'contact-sent'; this.recoveryMessage = data.message || '申请已发送给管理员，请等待管理员核实处理。' } catch (e) { this.recoveryError = e.message } finally { this.recoveryLoading = false } }
  }
}
</script>



