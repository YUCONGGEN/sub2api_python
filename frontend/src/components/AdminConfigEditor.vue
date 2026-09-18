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
      <div class="yaml-editor-toolbar">
        <span><i>·</i> 代表一个行首空格</span>
        <span><i>→</i> 代表 Tab（YAML 中不建议使用）</span>
        <span>拖动编辑区右下角可调整高度</span>
        <b>第 {{ cursorLine }} 行 · 第 {{ cursorColumn }} 列</b>
      </div>
      <div class="yaml-editor-shell" @wheel="handleEditorWheel">
        <pre class="yaml-line-numbers" aria-hidden="true" :style="gutterStyle">{{ lineNumbers }}</pre>
        <div class="yaml-editor-viewport">
          <pre class="yaml-visible-content" aria-hidden="true" :style="mirrorStyle">{{ visibleContent }}</pre>
          <textarea
            ref="yamlTextarea"
            v-model="content"
            class="yaml-editor"
            aria-label="application.yml 配置内容，左侧显示行号，行首空格以圆点显示"
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            wrap="off"
            @scroll="syncEditorScroll"
            @click="updateCursor"
            @keyup="updateCursor"
            @select="updateCursor"
            @input="updateCursor"
            @keydown.tab.prevent="handleTab"
          ></textarea>
        </div>
      </div>
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
    restartAvailable: false,
    editorScrollTop: 0,
    editorScrollLeft: 0,
    cursorLine: 1,
    cursorColumn: 1
  }),
  computed: {
    dirty () { return this.content !== this.savedContent },
    byteSize () { return new Blob([this.content || '']).size.toLocaleString('zh-CN') },
    lineCount () { return (this.content || '').split('\n').length },
    lineNumbers () { return Array.from({ length: this.lineCount }, (_, index) => index + 1).join('\n') },
    visibleContent () {
      return (this.content || '').split('\n').map(line => {
        const leading = (line.match(/^[ \t]*/) || [''])[0]
        const visibleLeading = leading.replace(/ /g, '·').replace(/\t/g, '→')
        return visibleLeading + line.slice(leading.length)
      }).join('\n')
    },
    mirrorStyle () { return { transform: `translate(${-this.editorScrollLeft}px, ${-this.editorScrollTop}px)` } },
    gutterStyle () { return { transform: `translateY(${-this.editorScrollTop}px)` } }
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
      this.editorScrollTop = 0
      this.editorScrollLeft = 0
      this.cursorLine = 1
      this.cursorColumn = 1
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
    syncEditorScroll (event) {
      this.editorScrollTop = event.target.scrollTop
      this.editorScrollLeft = event.target.scrollLeft
    },
    handleEditorWheel (event) {
      const editor = this.$refs.yamlTextarea
      if (!editor) return
      const scale = event.deltaMode === 1
        ? 20
        : event.deltaMode === 2
          ? Math.max(1, editor.clientHeight)
          : 1
      const deltaX = (event.shiftKey ? event.deltaY : event.deltaX) * scale
      const deltaY = (event.shiftKey ? 0 : event.deltaY) * scale
      const maxTop = Math.max(0, editor.scrollHeight - editor.clientHeight)
      const maxLeft = Math.max(0, editor.scrollWidth - editor.clientWidth)
      const nextTop = Math.max(0, Math.min(maxTop, editor.scrollTop + deltaY))
      const nextLeft = Math.max(0, Math.min(maxLeft, editor.scrollLeft + deltaX))
      if (nextTop === editor.scrollTop && nextLeft === editor.scrollLeft) return
      event.preventDefault()
      event.stopPropagation()
      editor.scrollTop = nextTop
      editor.scrollLeft = nextLeft
      this.editorScrollTop = nextTop
      this.editorScrollLeft = nextLeft
    },
    updateCursor () {
      const editor = this.$refs.yamlTextarea
      if (!editor) return
      const before = this.content.slice(0, editor.selectionStart)
      const lines = before.split('\n')
      this.cursorLine = lines.length
      this.cursorColumn = lines[lines.length - 1].length + 1
    },
    handleTab (event) {
      const editor = this.$refs.yamlTextarea
      if (!editor) return
      const start = editor.selectionStart
      const end = editor.selectionEnd
      if (event.shiftKey) {
        const lineStart = this.content.lastIndexOf('\n', start - 1) + 1
        const removable = this.content.slice(lineStart, lineStart + 2).match(/^ {1,2}/)?.[0].length || 0
        if (!removable) return
        this.content = this.content.slice(0, lineStart) + this.content.slice(lineStart + removable)
        this.$nextTick(() => {
          editor.setSelectionRange(Math.max(lineStart, start - removable), Math.max(lineStart, end - removable))
          this.updateCursor()
        })
        return
      }
      this.content = this.content.slice(0, start) + '  ' + this.content.slice(end)
      this.$nextTick(() => {
        editor.setSelectionRange(start + 2, start + 2)
        this.updateCursor()
      })
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
.yaml-editor-toolbar{display:flex;align-items:center;gap:16px;padding:8px 12px;border:1px solid #304858;border-bottom:0;border-radius:12px 12px 0 0;background:#172936;color:#8eabb9;font:9px var(--mono)}.yaml-editor-toolbar span{display:flex;align-items:center;gap:5px}.yaml-editor-toolbar i{color:#72b3cb;font-style:normal;font-weight:800}.yaml-editor-toolbar b{margin-left:auto;color:#abc5d1;font-weight:500}
.yaml-editor-shell{display:grid;grid-template-columns:auto minmax(0,1fr);grid-template-rows:minmax(0,1fr);height:clamp(430px,58vh,760px);min-height:320px;resize:vertical;overflow:hidden;border:1px solid #cbdce7;border-radius:0 0 12px 12px;background:#10202c;box-shadow:inset 0 1px 8px #07121b55}.yaml-line-numbers,.yaml-visible-content{box-sizing:border-box;margin:0;pointer-events:none;white-space:pre;font:12px/1.65 var(--mono);tab-size:2}.yaml-line-numbers{min-width:54px;height:100%;overflow:hidden;padding:18px 12px;color:#668391;text-align:right;will-change:transform}.yaml-editor-viewport{position:relative;min-width:0;min-height:0;height:100%;overflow:hidden;isolation:isolate;border-left:1px solid #2c4453}.yaml-visible-content{position:absolute;z-index:1;top:0;left:0;min-width:max-content;min-height:100%;padding:18px;color:#d7e7ed;will-change:transform}.yaml-editor{position:absolute;z-index:2;inset:0;display:block;width:100%;height:100%;resize:none;padding:18px;border:0;outline:none;background:transparent!important;color:transparent!important;-webkit-text-fill-color:transparent!important;caret-color:#f2fbff;font:12px/1.65 var(--mono);tab-size:2;white-space:pre;overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable;scrollbar-width:thin;scrollbar-color:#5e8194 #132633;touch-action:pan-x pan-y}.yaml-editor::-webkit-scrollbar{width:12px;height:12px}.yaml-editor::-webkit-scrollbar-track{background:#132633}.yaml-editor::-webkit-scrollbar-thumb{border:3px solid #132633;border-radius:999px;background:#5e8194}.yaml-editor::-webkit-scrollbar-corner{background:#132633}.yaml-editor::selection{background:#4e8ca866;color:transparent}.yaml-editor:focus{box-shadow:inset 0 0 0 2px #5794aa}
.config-editor-footer{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:14px}.config-save-state{display:flex;align-items:center;gap:10px;color:#6f8799;font-size:10px}.config-save-state span.dirty{color:#b17231}.config-save-state small{font:9px var(--mono);color:#91a2ae}.config-actions{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}.danger-btn{min-height:40px;padding:0 16px;border:1px solid #d99898;border-radius:9px;background:#fff5f5;color:#ae4f55;cursor:pointer;font-weight:700}.danger-btn:disabled{opacity:.45;cursor:not-allowed}.restart-note{margin:10px 0 0;color:#9a7350;font-size:10px}.config-state{display:grid;justify-items:center;gap:10px;padding:70px 20px;border:1px dashed #cfdee8;border-radius:12px;color:#7890a2}.config-state.error{color:#a7515c}.config-state span{font-size:11px}
:global(html[data-theme="dark"] .config-editor-panel){background:linear-gradient(145deg,#111e29,#152330)}:global(html[data-theme="dark"] .config-editor-head h2){color:#d4e1e8}:global(html[data-theme="dark"] .config-editor-head p){color:#8196a4}:global(html[data-theme="dark"] .config-editor-meta span){border-color:#2c4556;background:#142633;color:#aac7d6}:global(html[data-theme="dark"] .config-warning){border-color:#564a2f;background:#282419;color:#cfbd8b}:global(html[data-theme="dark"] .yaml-visible-content){color:#f1f8fb}:global(html[data-theme="dark"] .yaml-editor){background:transparent!important;color:transparent!important;-webkit-text-fill-color:transparent!important;caret-color:#fff!important}:global(html[data-theme="dark"] .danger-btn){border-color:#68424b;background:#2b1b21;color:#db8995}
@media(max-width:760px){.config-editor-panel{padding:15px}.config-editor-head,.config-editor-footer{align-items:stretch;flex-direction:column}.config-editor-meta{justify-items:start}.yaml-editor-shell{height:55vh}.yaml-editor,.yaml-line-numbers,.yaml-visible-content{font-size:11px}.yaml-line-numbers{min-width:46px;padding-inline:8px}.yaml-editor-toolbar{align-items:flex-start;flex-direction:column;gap:4px}.yaml-editor-toolbar b{margin-left:0}.config-actions{display:grid;grid-template-columns:1fr}.config-actions button{width:100%}}
</style>
