import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mappingCategory, filterMappings } from '../src/mappingFilters.mjs'

const rules = [
  { id: 1, name: '旧名字转sol', source_model: 'gpt-5.6-sol', target_model: 'gpt-6-luna', target_effort: 'high' },
  { id: 2, source_model: 'gpt-6-sol', target_model: '', target_effort: 'medium' },
  { id: 3, target_model: 'gpt-6-astra-2026-09-01' },
  { id: 4, target_model: 'custom' }
]
test('classification uses real target, not stale name, and handles preserved models', () => {
  assert.deepEqual(rules.map(mappingCategory), ['economy', 'balanced', 'capability', 'other'])
  assert.equal(mappingCategory({}), 'other')
})
test('search and selected filter preserve original selections and rows', () => {
  const ids = [1, 3]
  const snapshot = JSON.stringify(rules)
  assert.deepEqual(filterMappings(rules, 'economy', 'HIGH', ids).map(x => x.id), [1])
  assert.deepEqual(filterMappings(rules, 'balanced', '', ids), [])
  assert.deepEqual(filterMappings(rules, 'all', '', ids).map(x => x.id), [1, 3])
  assert.equal(filterMappings(rules, 'all', 'missing').length, 0)
  assert.deepEqual(ids, [1, 3])
  assert.equal(JSON.stringify(rules), snapshot)
})
