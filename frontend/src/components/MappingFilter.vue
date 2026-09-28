<template>
  <div class="mapping-filter">
    <div class="mapping-filter-tabs" aria-label="映射分类">
      <button v-for="tab in tabs" :key="tab.value" type="button" :class="{ active: category === tab.value }" :aria-pressed="String(category === tab.value)" @click="category = tab.value">{{ tab.label }} <span>{{ tab.count }}</span></button>
    </div>
    <div class="mapping-filter-tools">
      <input v-model="query" aria-label="搜索映射规则" placeholder="搜索模型、规则名或强度" />
      <label v-if="selectedIds !== null"><input v-model="onlySelected" type="checkbox" />只看已勾选（{{ selectedIds.length }}）</label>
      <small>显示 {{ visible.length }} / {{ items.length }} 条 · 按实际目标模型分类</small>
    </div>
    <slot :items="visible" />
    <p v-if="!visible.length" class="mapping-filter-empty">当前筛选下没有规则，请切换分类或清空搜索。</p>
    <small v-if="selectedIds !== null">切换分类不影响已勾选规则；保存分组后才生效。</small>
  </div>
</template>

<script>
import { mappingCategories, mappingCategory, filterMappings } from '../mappingFilters.mjs'
export default {
  props: { items: { type: Array, default: () => [] }, selectedIds: { type: Array, default: null } },
  data: () => ({ category: 'balanced', query: '', onlySelected: false }),
  computed: {
    tabs () { return mappingCategories.map(tab => ({ ...tab, count: this.items.filter(item => tab.value === 'all' || mappingCategory(item) === tab.value).length })) },
    visible () { return filterMappings(this.items, this.category, this.query, this.onlySelected ? this.selectedIds : null) }
  }
}
</script>

<style scoped>
.mapping-filter { margin-top: 16px; }
.mapping-filter-tabs, .mapping-filter-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; margin-bottom: 12px; }
.mapping-filter-tabs button { padding: 10px 14px; border: 1px solid #64869a55; border-radius: 10px; background: #8099ad0d; color: inherit; cursor: pointer; font: inherit; font-size: 12px; }
.mapping-filter-tabs button.active { background: #549d9526; border-color: #549d95; box-shadow: inset 0 -2px #549d95; }
.mapping-filter-tabs span { margin-left: 5px; opacity: .75; }
.mapping-filter-tools > input { width: 270px; max-width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #64869a55; border-radius: 10px; background: #8099ad0d; color: inherit; font: inherit; font-size: 12px; }
.mapping-filter-tools label { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer; }
small, .mapping-filter-empty { font-size: 12px; opacity: .75; }
</style>
