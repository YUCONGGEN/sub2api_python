import { test } from 'node:test'
import assert from 'node:assert/strict'
import { subscriptionGroups, loadSubscriptionAccounts } from '../src/subscriptionGroups.mjs'

const accounts = Array.from({ length: 10 }, (_, index) => ({ id: index + 1, enabled: index % 2 !== 0 }))

test('available subscriptions come first, retaining the original order within each group', () => {
  const snapshot = JSON.stringify(accounts)
  const groups = subscriptionGroups(accounts)
  assert.deepEqual(groups.map(group => group.id), ['available', 'disabled'])
  assert.deepEqual(groups[0].accounts.map(account => account.id), [2, 4, 6, 8, 10])
  assert.deepEqual(groups[1].accounts.map(account => account.id), [1, 3, 5, 7, 9])
  assert.equal(JSON.stringify(accounts), snapshot)
})

test('each group shows its first three subscriptions by default and expands independently', () => {
  assert.deepEqual(subscriptionGroups(accounts).map(group => group.visibleAccounts.length), [3, 3])
  assert.deepEqual(subscriptionGroups(accounts, { available: true }).map(group => group.visibleAccounts.length), [5, 3])
  assert.deepEqual(subscriptionGroups(accounts, { disabled: true }).map(group => group.visibleAccounts.length), [3, 5])
  assert.deepEqual(subscriptionGroups(accounts, { available: true, disabled: true }).map(group => group.visibleAccounts.length), [5, 5])
})

test('enabling or disabling an account moves it between groups without changing account data', () => {
  const rows = accounts.map(account => ({ ...account }))
  rows[1].enabled = false
  const groups = subscriptionGroups(rows)
  assert.deepEqual(groups[0].visibleAccounts.map(account => account.id), [4, 6, 8])
  assert.deepEqual(groups[1].visibleAccounts.map(account => account.id), [1, 2, 3])
  assert.equal(groups[1].accounts[1], rows[1])
})

test('empty and short groups do not require expansion', () => {
  assert.deepEqual(subscriptionGroups([]).map(group => group.visibleAccounts), [[], []])
  assert.deepEqual(subscriptionGroups(accounts.slice(0, 3)).map(group => group.visibleAccounts.length), [1, 2])
})

test('all API pages are loaded with the provider filter, preserving summary and avoiding duplicate accounts', async () => {
  const requests = []
  const data = await loadSubscriptionAccounts(async params => {
    requests.push(params)
    return { accounts: params.page === 1 ? accounts.slice(0, 6) : accounts.slice(5), pagination: { pages: 2 }, summary: { total: 10 } }
  }, 'openai')
  assert.deepEqual(requests, [
    { provider: 'openai', page: 1, page_size: 100 },
    { provider: 'openai', page: 2, page_size: 100 }
  ])
  assert.deepEqual(data.accounts, accounts)
  assert.deepEqual(data.summary, { total: 10 })
  assert.equal(subscriptionGroups(data.accounts, { available: true })[0].visibleAccounts.length, 5)
})

test('a failed page rejects the full load rather than displaying incomplete groups', async () => {
  await assert.rejects(loadSubscriptionAccounts(async ({ page }) => {
    if (page > 1) throw new Error('page failed')
    return { accounts: accounts.slice(0, 5), pagination: { pages: 2 } }
  }, ''), /page failed/)
})
