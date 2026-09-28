<template>
  <section class="mapping-preview">
    <h3>映射预览</h3>
    <p>使用已保存的用户分组规则，不发起上游请求。最终路由仍可能受兜底配置影响。</p>
    <form @submit.prevent="preview">
      <label>用户 ID<input v-model="userId" type="number" min="1" required /></label>
      <label>请求模型<input v-model.trim="model" list="mapping-model-options" maxlength="160" required /></label>
      <label>请求强度<input v-model.trim="effort" maxlength="16" placeholder="留空表示客户端未指定" /></label>
      <button class="secondary-btn" :disabled="loading">{{ loading ? '检查中…' : '检查映射' }}</button>
    </form>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="result" aria-live="polite">
      <p>生效分组：{{ result.group_name || result.group_id || '默认' }} · {{ result.reason }}</p>
      <p>{{ result.requested_model }} / {{ result.requested_effort || '未指定' }} → {{ result.mapped_model }} / {{ result.mapped_effort || '未指定' }}</p>
      <ul><li v-for="rule in result.rules" :key="rule.id">#{{ rule.id }} {{ rule.name }}：{{ rule.reason }}</li></ul>
    </div>
  </section>
</template>

<script>
import { api } from '../api'
export default {
  data: () => ({ userId: '', model: '', effort: '', loading: false, error: '', result: null }),
  watch: {
    userId () { this.result = null },
    model () { this.result = null },
    effort () { this.result = null }
  },
  methods: {
    async preview () {
      this.loading = true; this.error = ''; this.result = null
      try {
        const data = await api.previewModelMapping({ user_id: Number(this.userId), model: this.model, effort: this.effort })
        if (!data.ok) throw new Error(data.message || '预览失败')
        this.result = data.preview
      } catch (error) { this.error = error.message } finally { this.loading = false }
    }
  }
}
</script>

<style scoped>
.mapping-preview { padding: 20px; margin: 20px 0; border: 1px solid var(--border); border-radius: 14px; }
.mapping-preview p, .mapping-preview li { overflow-wrap: anywhere; font-size: 13px; }
form { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
label { display: flex; flex-direction: column; gap: 8px; flex: 1 1 180px; max-width: 300px; }
input { width: 100%; box-sizing: border-box; }
</style>
