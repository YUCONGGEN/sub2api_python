function tomlString (value) {
  return String(value == null ? '' : value)
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/[\r\n]+/g, ' ')
}

export function buildCodexConfig ({ appName, apiBaseUrl, codexConfig, apiKey }) {
  const codex = codexConfig || {}
  const selectedModel = codex.model === 'gpt-5.6-sol' ? 'gpt-6-sol' : (codex.model || 'gpt-6-sol')
  const providerKey = String(codex.model_provider || 'rose').replace(/[^a-zA-Z0-9_-]/g, '') || 'rose'
  const providerName = codex.provider_name || appName || '大模型接口管理'
  const providerBaseUrl = String(codex.provider_base_url || apiBaseUrl || '').replace(/\/$/, '')
  const codexBaseUrl = /\/v1$/i.test(providerBaseUrl) ? providerBaseUrl : `${providerBaseUrl}/v1`
  const bearerToken = String(apiKey || '').trim() || '<粘贴本站 API Key>'
  return `model = "${tomlString(selectedModel)}"
model_provider = "${tomlString(providerKey)}"
model_reasoning_effort = "${tomlString(codex.model_reasoning_effort || 'high')}"
approval_policy = "${tomlString(codex.approval_policy || 'on-request')}"
sandbox_mode = "${tomlString(codex.sandbox_mode || 'workspace-write')}"

[model_providers.${providerKey}]
name = "${tomlString(providerName)}"
base_url = "${tomlString(codexBaseUrl)}"
experimental_bearer_token = "${tomlString(bearerToken)}"
wire_api = "${tomlString(codex.wire_api || 'responses')}"
requires_openai_auth = false
supports_websockets = ${codex.supports_websockets === true}`
}

export function downloadTextFile (filename, content) {
  const blob = new Blob([String(content || '')], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}
