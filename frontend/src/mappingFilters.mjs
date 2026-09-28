export const mappingCategories = [
  { value: 'all', label: '全部' },
  { value: 'economy', label: '节省优先 · Luna' },
  { value: 'balanced', label: '均衡 · Sol' },
  { value: 'capability', label: '能力优先 · Astra' },
  { value: 'other', label: '其他规则' }
]

export function mappingCategory (rule) {
  const target = String(rule.target_model || rule.source_model || '').trim().toLowerCase().replace(/-\d{4}-\d{2}-\d{2}$/, '')
  return ({ 'gpt-6-luna': 'economy', 'gpt-6-sol': 'balanced', 'gpt-6-astra': 'capability' })[target] || 'other'
}

export function filterMappings (rules, category, query = '', selectedIds = null) {
  const keyword = query.trim().toLowerCase()
  return rules.filter(rule => (category === 'all' || mappingCategory(rule) === category) &&
    (!selectedIds || selectedIds.includes(rule.id)) &&
    (!keyword || [rule.id, rule.name, rule.source_model, rule.target_model, rule.source_effort, rule.target_effort].join(' ').toLowerCase().includes(keyword)))
}
