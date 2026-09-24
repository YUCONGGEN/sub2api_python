function t(o){return String(o??"").replace(/\\/g,"\\\\").replace(/"/g,'\\"').replace(/[\r\n]+/g," ")}function u({appName:o,apiBaseUrl:n,codexConfig:a,apiKey:r}){const e=a||{},l=e.model||"gpt-5.6-sol",c=String(e.model_provider||"rose").replace(/[^a-zA-Z0-9_-]/g,"")||"rose",s=e.provider_name||o||"大模型接口管理",i=String(e.provider_base_url||n||"").replace(/\/$/,""),m=/\/v1$/i.test(i)?i:`${i}/v1`,p=String(r||"").trim()||"<粘贴本站 API Key>";return`model = "${t(l)}"
model_provider = "${t(c)}"
model_reasoning_effort = "${t(e.model_reasoning_effort||"high")}"
approval_policy = "${t(e.approval_policy||"on-request")}"
sandbox_mode = "${t(e.sandbox_mode||"workspace-write")}"

[model_providers.${c}]
name = "${t(s)}"
base_url = "${t(m)}"
experimental_bearer_token = "${t(p)}"
wire_api = "${t(e.wire_api||"responses")}"
requires_openai_auth = false
supports_websockets = ${e.supports_websockets===!0}`}function g({appName:o,apiBaseUrl:n,codexConfig:a,apiKey:r}){const e=String(r||"").trim();if(!e)throw new Error("本站 API Key 不能为空，不能写入空密钥");const l=u({appName:o,apiBaseUrl:n,codexConfig:a,apiKey:e}),c=JSON.stringify({OPENAI_API_KEY:e},null,2),s=p=>{const $=new TextEncoder().encode(String(p));let d="";return $.forEach(h=>{d+=String.fromCharCode(h)}),btoa(d)},i=s(l),m=s(c);return`# Rose / Codex / CC Switch 配置导入脚本
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
$configText = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${i}'))
$authText = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${m}'))
$configTemp = "$configPath.tmp.$PID"
$authTemp = "$authPath.tmp.$PID"
[System.IO.File]::WriteAllText($configTemp, $configText, $utf8)
[System.IO.File]::WriteAllText($authTemp, $authText, $utf8)
Move-Item -LiteralPath $configTemp -Destination $configPath -Force
Move-Item -LiteralPath $authTemp -Destination $authPath -Force
Write-Host "Codex 配置已写入：$codexHome"
Write-Host "旧配置已备份为：config.toml.bak.$stamp 和 auth.json.bak.$stamp"
Write-Host '请重启 Codex 或重新打开终端后生效。CC Switch 可从当前 Codex 配置导入。'
`}function f(o,n){const a=new Blob([String(n||"")],{type:"text/plain;charset=utf-8"}),r=URL.createObjectURL(a),e=document.createElement("a");e.href=r,e.download=o,document.body.appendChild(e),e.click(),e.remove(),window.setTimeout(()=>URL.revokeObjectURL(r),0)}export{g as a,u as b,f as d};
