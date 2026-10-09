export function subscriptionGroups (accounts, expanded = {}) {
  return [
    { id: 'available', title: '可用订阅', accounts: accounts.filter(account => account.enabled) },
    { id: 'disabled', title: '停用订阅', accounts: accounts.filter(account => !account.enabled) }
  ].map(group => ({
    ...group,
    expanded: !!expanded[group.id],
    visibleAccounts: expanded[group.id] ? group.accounts : group.accounts.slice(0, 3)
  }))
}

// Group the whole pool, not just the current API page. Hidden cards are not
// rendered or queried for quota until their group is expanded.
export async function loadSubscriptionAccounts (fetchPage, provider) {
  const first = await fetchPage({ provider, page: 1, page_size: 100 })
  const accounts = new Map((first.accounts || []).map(account => [account.id, account]))
  const pages = Number(first.pagination?.pages || 1)
  for (let page = 2; page <= pages; page++) {
    const data = await fetchPage({ provider, page, page_size: 100 })
    for (const account of data.accounts || []) accounts.set(account.id, account)
  }
  return { ...first, accounts: [...accounts.values()] }
}
