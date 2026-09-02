<template>
  <section class="page">
    <div class="page-head"><div><div class="eyebrow">ACCESS / CREDENTIALS</div><h1>API 密钥</h1><p>每个应用使用独立密钥；明文只在创建时显示一次。</p></div><button class="primary-btn" :disabled="loading" @click="openCreate">＋ 新建密钥</button></div>
    <div v-if="error" class="page-state-error" role="alert"><div><b>密钥数据加载失败</b><span>{{ error }}</span></div><button class="secondary-btn" @click="load(false)">重试</button></div>
    <div class="panel key-summary"><div><span class="eyebrow">ENDPOINT</span><h2>{{ baseUrl }}/v1</h2></div><div><span class="eyebrow">本页活跃密钥</span><strong>{{ activeCount }}</strong></div><router-link class="secondary-btn" to="/docs">查看配置教程 ↗</router-link></div>
    <div class="panel keys-panel">
      <div class="panel-head"><div><span class="eyebrow">KEY DIRECTORY</span><h2>密钥列表</h2></div><button class="icon-btn keys-refresh" :class="{ spinning: refreshing }" :disabled="refreshing" title="刷新" aria-label="刷新密钥列表" @click.prevent="load">↻</button></div>
      <div v-if="refreshing && !keys.length" class="loading-skeleton" aria-live="polite">正在读取密钥…</div>
      <div v-else class="table-wrap"><table><thead><tr><th>名称</th><th>API 密钥</th><th>状态</th><th>有效期</th><th>最近使用</th><th>操作</th></tr></thead><tbody><tr v-for="item in keys" :key="item.id"><td><strong>{{ item.name }}</strong><small v-if="item.id === freshKeyId" class="fresh-key-label">仅本次可复制</small></td><td><code>{{ item.id === freshKeyId && item.api_key ? item.api_key : item.masked_key }}</code><button v-if="item.id === freshKeyId && item.api_key" class="copy-btn" @click="copy(item.api_key)">复制明文</button></td><td><span :class="['status', keyUsable(item) ? 'success' : 'pending']">{{ keyUsable(item) ? '已启用' : item.enabled ? '已过期' : '已撤销' }}</span></td><td>{{ item.expires_at ? format(item.expires_at) : '永不过期' }}</td><td>{{ item.last_used ? format(item.last_used) : '暂无使用' }}</td><td><button v-if="item.enabled" class="text-btn danger" @click="revoke(item)">撤销</button><span v-else class="muted">记录保留</span></td></tr></tbody></table></div>
      <div v-if="!refreshing && !keys.length && !error" class="empty compact-empty">暂无 API 密钥，请点击右上角新建。</div>
      <div class="pagination" v-if="pagination.pages > 1"><button class="secondary-btn" :disabled="pagination.page <= 1" @click="changePage(pagination.page - 1)">上一页</button><span>第 {{ pagination.page }} / {{ pagination.pages }} 页，共 {{ pagination.total }} 条</span><button class="secondary-btn" :disabled="pagination.page >= pagination.pages" @click="changePage(pagination.page + 1)">下一页</button></div>
    </div>
    <div v-if="createOpen" class="modal-backdrop" @click.self="closeCreate"><div ref="createDialog" class="modal-card" role="dialog" aria-modal="true" aria-labelledby="create-key-title" tabindex="-1" @keydown.esc.prevent="closeCreate" @keydown="trapFocus"><button class="modal-close" aria-label="关闭" @click="closeCreate">×</button><span class="eyebrow">NEW CREDENTIAL</span><h2 id="create-key-title">新建 API 密钥</h2><p class="panel-note">密钥明文只显示一次，请创建后立即保存。</p><label>密钥名称<input v-model.trim="createName" maxlength="64" autofocus placeholder="例如：生产环境 / Codex" @keyup.enter="quickCreate" /></label><label>过期时间 <span class="optional">可选</span><input v-model="createExpiresAt" type="datetime-local" /></label><p v-if="createError" class="form-error" role="alert">{{ createError }}</p><div class="feedback-actions"><button class="secondary-btn" @click="closeCreate">取消</button><button class="primary-btn" :disabled="loading || !createName" @click="quickCreate">{{ loading ? '创建中…' : '创建密钥' }}</button></div></div></div>
  </section>
</template>
<script>
import { api } from '../api'
import { copyToClipboard, notify, askConfirm, focusDialog, trapDialogFocus } from '../ui'
export default {
  props: { apiBaseUrl: String },
  data () { return { keys: [], pagination: { page: 1, pages: 1, total: 0 }, loading: false, refreshing: false, error: '', freshKeyId: null, baseUrl: this.apiBaseUrl || '', createOpen: false, createName: '', createExpiresAt: '', createError: '', dialogReturnFocus: null } },
  computed: { activeCount () { return this.keys.filter(item => this.keyUsable(item)).length } },
  watch: { apiBaseUrl (value) { if (value) this.baseUrl = value.replace(/\/$/, '') } },
  created () { this.load(false) },
  methods: {
    async load (preservePosition = true) { if (this.refreshing) return; const scrollX = window.scrollX; const scrollY = window.scrollY; this.refreshing = true; this.error = ''; try { const d = await api.keys({ page: this.pagination.page, page_size: 5 }); this.keys = d.keys || []; this.pagination = d.pagination || this.pagination; this.freshKeyId = null } catch (e) { this.error = e.message } finally { this.refreshing = false; if (preservePosition) this.$nextTick(() => window.scrollTo(scrollX, scrollY)) } },
    changePage (page) { this.pagination.page = page; this.load(true) },
    openCreate () { this.dialogReturnFocus = document.activeElement; this.createName = ''; this.createExpiresAt = ''; this.createError = ''; this.createOpen = true; this.$nextTick(() => focusDialog(this.$refs.createDialog)) },
    closeCreate () { this.createOpen = false; this.$nextTick(() => this.dialogReturnFocus?.focus?.()) },
    trapFocus (event) { trapDialogFocus(event, this.$refs.createDialog) },
    async quickCreate () { if (!this.createName) { this.createError = '请输入密钥名称'; return }; this.loading = true; try { const body = { name: this.createName }; if (this.createExpiresAt) body.expires_at = new Date(this.createExpiresAt).toISOString(); const d = await api.createKey(body); if (!d.ok || !d.key) throw new Error(d.message || '创建密钥失败'); this.createOpen = false; this.freshKeyId = d.key.id; if (d.key.api_key) window.sessionStorage.setItem('rose_fresh_api_key', d.key.api_key); this.pagination.page = 1; this.pagination.total = Number(this.pagination.total || 0) + 1; this.pagination.pages = Math.max(1, Math.ceil(this.pagination.total / 5)); this.keys = [d.key, ...this.keys.filter(item => item.id !== d.key.id)].slice(0, 5); notify('密钥已创建并填入配置教程；刷新后不能再次查看明文', 'success') } catch (e) { this.createError = e.message; notify(e.message, 'error') } finally { this.loading = false } },
    async revoke (item) { if (!await askConfirm(`确定撤销“${item.name}”吗？使用它的客户端会立即停止调用。`)) return; try { const d = await api.revokeKey(item.id); if (!d.ok) throw new Error(d.message); item.enabled = false; notify('密钥已撤销，历史记录已保留', 'success') } catch (e) { notify(e.message, 'error') } },
    keyUsable (item) { return !!item.enabled && (!item.expires_at || new Date(item.expires_at).getTime() > Date.now()) },
    async copy (value) { const copied = await copyToClipboard(value); notify(copied ? '已复制' : '复制失败，请检查浏览器权限', copied ? 'success' : 'error') },
    format (value) { return value ? new Date(value).toLocaleString('zh-CN') : '-' }
  }
}
</script>
