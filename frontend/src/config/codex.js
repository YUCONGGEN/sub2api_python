function tomlString (value) {
  return String(value == null ? '' : value)
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/[\r\n]+/g, ' ')
}

export function buildCodexConfig ({ appName, apiBaseUrl, codexConfig, apiKey }) {
  const codex = codexConfig || {}
  const selectedModel = codex.model || 'gpt-5.6-sol'
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

// Build a local, user-initiated importer.  The browser cannot write to
// %USERPROFILE% directly, so the script is an explicit fallback for browsers
// without the File System Access API and also keeps CC Switch compatible by
// writing Codex's standard auth.json alongside config.toml.
export function buildCodexImportScript ({ appName, apiBaseUrl, codexConfig, apiKey }) {
  const token = String(apiKey || '').trim()
  if (!token) throw new Error('本站 API Key 不能为空，不能写入空密钥')
  const configText = buildCodexConfig({ appName, apiBaseUrl, codexConfig, apiKey: token })
  const authText = JSON.stringify({ OPENAI_API_KEY: token }, null, 2)
  const encode = value => {
    const bytes = new TextEncoder().encode(String(value))
    let binary = ''
    bytes.forEach(byte => { binary += String.fromCharCode(byte) })
    return btoa(binary)
  }
  const configBase64 = encode(configText)
  const authBase64 = encode(authText)
  return `# Rose / Codex / CC Switch 配置导入脚本
# 运行方式：PowerShell 中执行 .\\import-codex.ps1
# 脚本会备份现有 config.toml 和 auth.json，再原子替换配置。
$ErrorActionPreference = 'Stop'
$codexHome = Join-Path $env:USERPROFILE '.codex'
New-Item -ItemType Directory -Force -Path $codexHome | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$configPath = Join-Path $codexHome 'config.toml'
$authPath = Join-Path $codexHome 'auth.json'
foreach ($path in @($configPath, $authPath)) {
  if (Test-Path -LiteralPath $path) {
    Copy-Item -LiteralPath $path -Destination "$path.bak.$stamp" -Force
  }
}
$utf8 = [System.Text.UTF8Encoding]::new($false)
$configText = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${configBase64}'))
$authText = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${authBase64}'))
$configTemp = "$configPath.tmp.$PID"
$authTemp = "$authPath.tmp.$PID"
[System.IO.File]::WriteAllText($configTemp, $configText, $utf8)
[System.IO.File]::WriteAllText($authTemp, $authText, $utf8)
Move-Item -LiteralPath $configTemp -Destination $configPath -Force
Move-Item -LiteralPath $authTemp -Destination $authPath -Force
Write-Host "Codex 配置已写入：$codexHome"
Write-Host "旧配置已备份为：config.toml.bak.$stamp 和 auth.json.bak.$stamp"
Write-Host '请重启 Codex 或重新打开终端后生效。CC Switch 可从当前 Codex 配置导入。'
`
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
