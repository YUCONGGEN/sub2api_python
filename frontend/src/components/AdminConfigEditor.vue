<template>
  <section class="panel config-editor-panel">
    <div class="config-editor-head">
      <div>
        <span class="eyebrow">SERVER CONFIGURATION</span>
        <h2>后端 YAML 配置</h2>
        <p>仅管理员可查看。保存前自动校验 YAML，并保留 <code>{{ backupFilename }}</code> 备份；修改需重启后端才会生效。</p>
      </div>
      <div class="config-editor-meta">
        <span>{{ filename || 'application.yml' }}</span>
        <small v-if="modifiedAt">更新于 {{ formatDate(modifiedAt) }}</small>
      </div>
    </div>

    <div v-if="loading" class="config-state">正在读取服务器配置…</div>
    <div v-else-if="loadError" class="config-state error">
      <strong>配置读取失败</strong>
      <span>{{ loadError }}</span>
      <button class="secondary-btn" @click="loadConfig">重新加载</button>
    </div>
    <template v-else>
      <div class="config-warning"><b>敏感配置</b><span>此处可能包含密钥和数据库密码，请勿复制给无关人员。</span></div>
      <textarea
        v-model="content"
        class="yaml-editor"
        aria-label="application.yml 配置内容"
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
      ></textarea>
      <div class="config-editor-footer">
        <div class="config-save-state">
          <span :class="{ dirty }">{{ dirty ? '有未保存修改' : '内容已与服务器同步' }}</span>
          <small>{{ byteSize }} 字节</small>
        </div>
        <div class="config-actions">
          <button class="secondary-btn" :disabled="saving || restarting" @click="reloadConfig">重新加载</button>
          <button class="primary-btn" :disabled="!dirty || saving || restarting" @click="saveConfig">{{ saving ? '校验并保存中…' : '校验并保存' }}</button>
          <button class="danger-btn" :disabled="dirty || saving || restarting || !restartAvailable" :title="restartAvailable ? '重启后端以应用配置' : '服务器未提供 restart_backend.sh'" @click="restartBackend">{{ restarting ? '正在安排重启…' : '重启后端' }}</button>
        </div>
      </div>
      <p v-if="!restartAvailable" class="restart-note">当前环境未检测到可用的 <code>restart_backend.sh</code>，可以保存配置，但不能从网页重启。</p>
    </template>
  </section>
</template>

<script>
import { api } from '../api'
import { askConfirm, notify } from '../ui'

export default {
  data: () => ({
    loading: true,
    saving: false,
    restarting: false,
    loadError: '',
    filename: 'application.yml',
    backupFilename: 'application.yml.bak',
    modifiedAt: '',
    revision: '',
    content: '',
    savedContent: '',
    restartAvailable: false
  }),
  computed: {
    dirty () { return this.content !== this.savedContent },
    byteSize () { return new Blob([this.content || '']).size.toLocaleString('zh-CN') }
  },
  created () {
    this.loadConfig()
    window.addEventListener('beforeunload', this.warnUnsaved)
  },
  beforeDestroy () {
    window.removeEventListener('beforeunload', this.warnUnsaved)
  },
  methods: {
    warnUnsaved (event) {
      if (!this.dirty) return
      event.preventDefault()
      event.returnValue = ''
    },
    applyConfig (data) {
      this.filename = data.filename || 'application.yml'
      this.backupFilename = data.backup_filename || 'application.yml.bak'
      this.modifiedAt = data.modified_at || ''
      this.revision = data.revision || ''
      this.content = data.content || ''
      this.savedContent = this.content
      this.restartAvailable = !!data.restart_available
    },
    async loadConfig () {
      this.loading = true
      this.loadError = ''
      try {
        this.applyConfig(await api.adminApplicationConfig())
      } catch (error) {
        this.loadError = error.message || '无法读取配置'
      } finally {
        this.loading = false
      }
    },
    async reloadConfig () {
      if (this.dirty && !await askConfirm('重新加载会丢弃当前未保存的修改，确定继续吗？')) return
      await this.loadConfig()
    },
    async saveConfig () {
      if (!await askConfirm('确定保存 application.yml 吗？保存会覆盖服务器配置并生成备份，但不会立即重启。')) return
      this.saving = true
      try {
        const data = await api.saveAdminApplicationConfig({ content: this.content, revision: this.revision })
        this.applyConfig(data)
        notify('application.yml 已保存，重启后端后生效', 'success')
      } catch (error) {
        notify(error.message || '配置保存失败', 'error')
      } finally {
        this.saving = false
      }
    },
    async restartBackend () {
      if (!await askConfirm('确定现在重启后端吗？正在进行的模型请求可能会被中断。')) return
      this.restarting = true
      try {
        await api.restartBackend()
        notify('后端重启已安排，请稍后刷新页面', 'success')
        window.setTimeout(() => { this.restarting = false }, 8000)
      } catch (error) {
        this.restarting = false
        notify(error.message || '后端重启失败', 'error')
      }
    },
    formatDate (value) { return value ? new Date(value).toLocaleString('zh-CN') : '-' }
  }
}
</script>

<style scoped>
.config-editor-panel{margin-top:18px;padding:22px;background:linear-gradient(145deg,#fbfeff,#f8f8fe)}
.config-editor-head{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:16px}.config-editor-head h2{margin:5px 0 7px;color:#263f59;font-size:20px}.config-editor-head p{margin:0;max-width:760px;color:#73899a;font-size:11px;line-height:1.65}.config-editor-head code,.restart-note code{font:10px var(--mono);color:#486d87}
.config-editor-meta{display:grid;justify-items:end;gap:5px;flex:none}.config-editor-meta span{padding:7px 10px;border:1px solid #d5e2eb;border-radius:8px;background:#fff;color:#315a76;font:10px var(--mono)}.config-editor-meta small{color:#8a9daa;font:9px var(--mono)}
.config-warning{display:flex;align-items:center;gap:10px;margin-bottom:10px;padding:9px 12px;border:1px solid #ead9ad;border-radius:9px;background:#fff9e9;color:#8a7137;font-size:10px}.config-warning b{font-size:10px}
.yaml-editor{display:block;width:100%;min-height:clamp(430px,58vh,760px);resize:vertical;padding:18px;border:1px solid #cbdce7;border-radius:12px;outline:none;background:#10202c;color:#d7e7ed;box-shadow:inset 0 1px 8px #07121b55;font:12px/1.65 var(--mono);tab-size:2;white-space:pre}.yaml-editor:focus{border-color:#5794aa;box-shadow:0 0 0 3px #4f91aa1c,inset 0 1px 8px #07121b55}
.config-editor-footer{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:14px}.config-save-state{display:flex;align-items:center;gap:10px;color:#6f8799;font-size:10px}.config-save-state span.dirty{color:#b17231}.config-save-state small{font:9px var(--mono);color:#91a2ae}.config-actions{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}.danger-btn{min-height:40px;padding:0 16px;border:1px solid #d99898;border-radius:9px;background:#fff5f5;color:#ae4f55;cursor:pointer;font-weight:700}.danger-btn:disabled{opacity:.45;cursor:not-allowed}.restart-note{margin:10px 0 0;color:#9a7350;font-size:10px}.config-state{display:grid;justify-items:center;gap:10px;padding:70px 20px;border:1px dashed #cfdee8;border-radius:12px;color:#7890a2}.config-state.error{color:#a7515c}.config-state span{font-size:11px}
:global(html[data-theme="dark"] .config-editor-panel){background:linear-gradient(145deg,#111e29,#152330)}:global(html[data-theme="dark"] .config-editor-head h2){color:#d4e1e8}:global(html[data-theme="dark"] .config-editor-head p){color:#8196a4}:global(html[data-theme="dark"] .config-editor-meta span){border-color:#2c4556;background:#142633;color:#aac7d6}:global(html[data-theme="dark"] .config-warning){border-color:#564a2f;background:#282419;color:#cfbd8b}:global(html[data-theme="dark"] .danger-btn){border-color:#68424b;background:#2b1b21;color:#db8995}
@media(max-width:760px){.config-editor-panel{padding:15px}.config-editor-head,.config-editor-footer{align-items:stretch;flex-direction:column}.config-editor-meta{justify-items:start}.yaml-editor{min-height:55vh;font-size:11px}.config-actions{display:grid;grid-template-columns:1fr}.config-actions button{width:100%}}
</style>
