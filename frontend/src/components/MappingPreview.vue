<template>
  <section class="mapping-preview">
    <h3>映射预览</h3>
    <p>使用已保存的用户分组规则，不发起上游请求。最终路由仍可能受兜底配置影响。</p>
    <form @submit.prevent="preview">
      <div class="user-picker">
        <label>用户<input v-model.trim="keyword" placeholder="输入用户名搜索" @input="scheduleSearch" /></label>
        <AppSelect v-model="userId" :options="userOptions" :disabled="usersLoading" placeholder="请选择用户" aria-label="预览用户" />
        <small v-if="usersError" role="alert">{{ usersError }} <button type="button" @click="loadUsers(1)">重试</button></small>
        <small v-else-if="!usersLoading && !users.length">没有找到用户</small>
        <div v-if="pages > 1" class="user-pages"><button type="button" :disabled="usersLoading || page <= 1" @click="loadUsers(page - 1)">上一页</button><span>{{ page }} / {{ pages }}</span><button type="button" :disabled="usersLoading || page >= pages" @click="loadUsers(page + 1)">下一页</button></div>
      </div>
      <label>请求模型<input v-model.trim="model" list="mapping-model-options" maxlength="160" required /></label>
      <label>请求强度<input v-model.trim="effort" maxlength="16" placeholder="留空表示客户端未指定" /></label>
      <button class="secondary-btn" :disabled="loading || !userId || usersLoading">{{ loading ? '检查中…' : '检查映射' }}</button>
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
import AppSelect from './AppSelect.vue'
export default {
  components: { AppSelect },
  data: () => ({ userId: '', keyword: '', users: [], usersLoading: false, usersError: '', page: 1, pages: 1, searchTimer: null, searchVersion: 0, model: '', effort: '', loading: false, error: '', result: null }),
  computed: {
    userOptions () { return this.users.map(user => ({ value: user.id, label: user.username, description: user.group_name || '' })) }
  },
  mounted () { this.loadUsers(1) },
  beforeDestroy () { clearTimeout(this.searchTimer); this.searchVersion++ },
  watch: {
    userId () { this.result = null },
    model () { this.result = null },
    effort () { this.result = null }
  },
  methods: {
    scheduleSearch () {
      clearTimeout(this.searchTimer)
      this.searchVersion++; this.userId = ''; this.users = []; this.usersLoading = true
      this.searchTimer = setTimeout(() => this.loadUsers(1), 300)
    },
    async loadUsers (page) {
      const version = ++this.searchVersion
      this.usersLoading = true; this.usersError = ''; this.userId = ''
      try {
        const data = await api.users(this.keyword, { page, page_size: 20 })
        if (version !== this.searchVersion) return
        if (!data.ok) throw new Error(data.message || '用户加载失败')
        this.users = data.users || []; this.page = data.pagination?.page || 1; this.pages = data.pagination?.pages || 1
      } catch (error) { if (version === this.searchVersion) { this.users = []; this.usersError = error.message } } finally { if (version === this.searchVersion) this.usersLoading = false }
    },
    async preview () {
      if (!this.userId) return
      const snapshot = JSON.stringify([this.userId, this.model, this.effort])
      this.loading = true; this.error = ''; this.result = null
      try {
        const data = await api.previewModelMapping({ user_id: Number(this.userId), model: this.model, effort: this.effort })
        if (!data.ok) throw new Error(data.message || '预览失败')
        if (snapshot === JSON.stringify([this.userId, this.model, this.effort])) this.result = data.preview
      } catch (error) { this.error = error.message } finally { this.loading = false }
    }
  }
}
</script>

<style scoped>
.mapping-preview { padding: 20px; margin: 20px 0; border: 1px solid var(--border); border-radius: 14px; }
.mapping-preview p, .mapping-preview li { overflow-wrap: anywhere; font-size: 13px; }
form { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-start; }
label { display: flex; flex-direction: column; gap: 8px; flex: 1 1 180px; max-width: 300px; }
input { width: 100%; box-sizing: border-box; min-height: 48px; padding: 12px 14px; border: 1px solid #58758766; border-radius: 12px; color: inherit; background: #829baa12; font: inherit; }
.user-picker { display: grid; gap: 8px; flex: 1 1 220px; max-width: 300px; }
.user-picker label { display: flex; }
.user-pages { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; }
.user-pages button { color: inherit; background: transparent; border: 1px solid #58758766; border-radius: 6px; padding: 5px; cursor: pointer; }
form > button { margin-top: 27px; }
</style>
