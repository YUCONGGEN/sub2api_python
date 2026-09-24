<template>
  <section class="page">
    <div class="page-head"><div><div class="eyebrow">DOCS / QUICK START</div><h1>配置教程</h1><p>使用注册时自动生成或后续新建的 API 密钥接入 OpenAI 兼容接口或 Codex。</p></div></div>
    <div class="docs-layout"><aside class="docs-nav"><div class="docs-nav-title">快速开始</div><a class="active" href="#unified-api">统一 API</a><a v-if="showTraeTutorial" href="#traecn">TraeCN</a><a href="#codex">Codex 调用 GPT</a></aside>
      <div class="docs-content"><section id="unified-api"><span class="eyebrow">01 / ENDPOINT</span><h2>OpenAI 兼容地址</h2><p>使用 OpenAI Chat Completions 格式时，API 地址只填写到 <code>/v1</code>，不要再追加 <code>/chat/completions</code>。</p><div class="code-block"><div class="code-head"><span>BASE URL</span><button @click="copy(`${baseUrl}/v1`)">复制</button></div><code>{{ baseUrl }}/v1</code></div></section>
        <section><span class="eyebrow">02 / REQUEST</span><h2>通用 OpenAI 配置</h2><div class="code-block large"><div class="code-head"><span>model_id / url / key</span><button @click="copy(openaiConfig)">复制</button></div><pre>{{ openaiConfig }}</pre></div></section>
        <section v-if="showTraeTutorial" id="traecn" class="trae-tutorial"><span class="eyebrow">03 / TRAECN</span><h2>TraeCN 软件接入</h2><p>下面按 TraeCN 当前界面说明配置流程。先在本系统的“API 密钥”页面创建密钥，再把密钥粘贴到 TraeCN；密钥删除后会立即失效。</p>
          <div class="trae-steps">
            <article class="trae-step"><div class="trae-step-index">01</div><div class="trae-step-copy"><h3>关闭自动模式</h3><p>打开 TraeCN 的 Agent 对话框，点击底部的 <strong>Auto</strong>，关闭 Auto Mode，改为手动选择模型。</p></div><figure><img src="/traecn-tutorial/1.png" alt="TraeCN Agent 中打开 Auto 模式菜单" loading="lazy" /></figure></article>
            <article class="trae-step"><div class="trae-step-index">02</div><div class="trae-step-copy"><h3>确认使用手动模型</h3><p>在 Auto Mode 菜单中将开关关闭。关闭后，底部会显示当前手动模型选择入口。</p></div><figure><img src="/traecn-tutorial/2.png" alt="TraeCN 关闭 Auto Mode" loading="lazy" /></figure></article>
            <article class="trae-step"><div class="trae-step-index">03</div><div class="trae-step-copy"><h3>进入模型管理</h3><p>点击模型选择框，在模型列表底部选择 <strong>添加模型</strong>，进入模型管理页面。</p></div><figure><img src="/traecn-tutorial/3.png" alt="TraeCN 模型列表中的添加模型入口" loading="lazy" /></figure></article>
            <article class="trae-step"><div class="trae-step-index">04</div><div class="trae-step-copy"><h3>点击添加模型</h3><p>在模型管理页点击“添加模型”，TraeCN 会显示可接入的服务商和自定义模型选项。</p></div><figure><img src="/traecn-tutorial/4.png" alt="TraeCN 模型管理页面" loading="lazy" /></figure></article>
            <article class="trae-step"><div class="trae-step-index">05</div><div class="trae-step-copy"><h3>选择自定义模型</h3><p>选择“自定义模型”，不要选择内置服务商，以便接入本系统的 OpenAI 兼容网关。</p></div><figure><img src="/traecn-tutorial/5.png" alt="TraeCN 添加模型时选择自定义模型" loading="lazy" /></figure></article>
            <article class="trae-step"><div class="trae-step-index">06</div><div class="trae-step-copy"><h3>填写接口信息</h3><p>API 格式选择 <strong>OpenAI Chat Completions 格式</strong>；自定义请求地址填上方 BASE URL，模型 ID 填模型广场中的实际 ID，API 密钥填本系统创建的密钥。显示名称可按项目需要填写。</p><div class="trae-inline-code"><span>请求地址</span><code>{{ baseUrl }}/v1</code><button @click="copy(`${baseUrl}/v1`)">复制</button></div></div><figure><img src="/traecn-tutorial/6.png" alt="TraeCN 自定义模型接口和密钥配置" loading="lazy" /></figure></article>
            <article class="trae-step trae-step-last"><div class="trae-step-index">07</div><div class="trae-step-copy"><h3>设置图片输入能力</h3><p>在高级配置中按模型实际能力选择。模型支持图片输入时选择“支持”；<strong>如果模型不支持图片输入，请勾选“不支持”</strong>，否则 TraeCN 可能会发送模型无法处理的图片消息。</p></div><figure><img src="/traecn-tutorial/7.png" alt="TraeCN 高级配置中选择不支持图片输入" loading="lazy" /><figcaption>模型不支持图片输入时，请选择“不支持”。</figcaption></figure></article>
          </div>
          <div class="docs-callout trae-callout"><b>提示</b><span>模型 ID、计费和图片能力以模型广场展示为准。接入后可先发送一条短消息测试；遇到 401 请检查 API 密钥，遇到 404 请确认地址包含 <code>/v1</code> 且没有重复追加路径。</span></div>
        </section>
        <section id="codex" class="codex-tutorial">
          <span class="eyebrow">{{ showTraeTutorial ? '04' : '03' }} / CODEX</span>
          <h2>在 Codex 中调用 GPT 模型</h2>
          <p>Codex 通过 Responses API 调用本站模型。下面以 Windows 为例，将本站创建的 API Key 直接写入用户级 <code>config.toml</code>，无需额外设置环境变量。<strong>配置中的 <code>base_url</code> 必须指向本站网关，不能填写网关内部使用的第三方上游地址。</strong></p>

          <div class="codex-overview">
            <div><span>接口协议</span><strong>Responses API</strong><small>Codex 自定义供应商仅使用 responses 协议</small></div>
            <div><span>接口地址</span><strong>{{ baseUrl }}/v1</strong><small>只保留一个 /v1</small></div>
            <div><span>默认模型</span><strong>{{ selectedCodexModel }}</strong><small>也可在启动时用 -m 临时切换</small></div>
          </div>

          <div class="codex-steps">
            <article class="codex-step">
              <div class="codex-step-index">01</div>
              <div class="codex-step-body"><h3>确认本站 API Key</h3><p>新注册账户已经自动生成首枚密钥，并会在本标签页中自动填入下方配置。如果需要更换或首枚密钥已离开当前页面，请到“API 密钥”新建一枚。这里只使用本站生成的 <code>sk-api-...</code>，不要填写 OpenAI 订阅 Token、Codex <code>auth.json</code> 或浏览器 Cookie。</p><router-link class="secondary-btn codex-link" to="/keys">管理 API 密钥 ↗</router-link></div>
            </article>

            <article class="codex-step">
              <div class="codex-step-index">02</div>
              <div class="codex-step-body">
                <h3>安装 Codex（Windows）</h3>
                <p>推荐安装 Windows 桌面版；如果主要在终端工作，也可以只安装 Codex CLI。桌面版与 CLI 共用用户目录下的配置文件。</p>
                <div class="codex-install-options">
                  <div class="codex-install-option featured">
                    <div class="codex-install-option-head"><span>推荐</span><b>Windows 桌面版</b></div>
                    <p>打开 OpenAI Codex 官方页面，点击“下载 Windows 版”完成安装；也可在 PowerShell 中通过 Microsoft Store 安装。</p>
                    <a class="secondary-btn codex-link" href="https://openai.com/zh-Hans-CN/codex/" target="_blank" rel="noopener">打开官方下载页 ↗</a>
                    <div class="code-block large"><div class="code-head"><span>Windows PowerShell / winget</span><button @click="copy(codexWindowsInstallText)">复制</button></div><pre>{{ codexWindowsInstallText }}</pre></div>
                  </div>
                  <div class="codex-install-option">
                    <div class="codex-install-option-head"><span>终端</span><b>Codex CLI</b></div>
                    <p>先安装 Node.js，再用 npm 全局安装 Codex CLI。最后一行应输出版本号。</p>
                    <div class="code-block large"><div class="code-head"><span>Windows PowerShell / npm</span><button @click="copy(codexInstallText)">复制</button></div><pre>{{ codexInstallText }}</pre></div>
                  </div>
                </div>
              </div>
            </article>

            <article class="codex-step">
              <div class="codex-step-index">03</div>
              <div class="codex-step-body"><h3>手动配置用户级 config.toml</h3><p>浏览器不能直接修改本机文件。请复制完整配置或下载 <code>config.toml</code>，然后手动替换对应系统用户目录中的文件。</p><ul class="codex-config-paths"><li><span>Windows 路径：</span><code>C:\Users\用户名\.codex\config.toml</code></li><li><span>Mac/Linux 路径：</span><code>~/.codex/config.toml</code></li></ul><p>写入前必须填入非空本站 API Key。配置只在当前浏览器内存中生成，不会以明文保存到服务器。</p><div class="codex-key-field"><label for="codex-api-key">本站 API Key</label><input id="codex-api-key" v-model.trim="apiKey" autocomplete="off" spellcheck="false" placeholder="粘贴 sk-api-...；刚注册时会自动填入" /><small>{{ apiKey ? '已填入配置，下载前请确认当前设备可信。' : '尚未填入，禁止下载会写入空密钥的配置。' }}</small></div><div class="code-block large"><div class="code-head"><span>config.toml 完整文件内容（全部替换）</span><div class="code-head-actions"><button @click="copyCodexConfig">复制完整配置</button><button class="download-config-btn" @click="downloadConfig">下载 config.toml</button></div></div><pre>{{ codexConfigText }}</pre></div><div class="codex-file-command"><code>notepad "$env:USERPROFILE\.codex\config.toml"</code><button @click="copy(codexOpenConfigCommand)">复制打开命令</button></div></div>
            </article>

            <article class="codex-step">
              <div class="codex-step-index">04</div>
              <div class="codex-step-body"><h3>启动 Codex 并调用 GPT</h3><p><strong>桌面版：</strong>配置完成后完全退出并重新打开应用，选择“添加项目”或按 <code>Ctrl+O</code> 打开项目文件夹，再创建任务。<strong>CLI：</strong>进入项目目录运行 <code>codex</code>；<code>codex exec</code> 用于执行一次性任务。首次测试建议只让模型返回一行文字。</p><div class="code-block large"><div class="code-head"><span>CLI 交互调用 / 单次调用</span><button @click="copy(codexRunText)">复制</button></div><pre>{{ codexRunText }}</pre></div></div>
            </article>

            <article class="codex-step">
              <div class="codex-step-index">05</div>
              <div class="codex-step-body"><h3>切换 GPT 模型</h3><p>永久切换请修改配置中的 <code>model</code>；只切换当前任务可使用 <code>codex -m 模型ID</code>。模型 ID 必须与“模型广场”显示内容完全一致。</p><div class="codex-model-list"><code v-for="model in codexModelIds" :key="model">{{ model }}</code></div><div class="code-block large"><div class="code-head"><span>临时指定模型</span><button @click="copy(codexModelCommand)">复制</button></div><pre>{{ codexModelCommand }}</pre></div></div>
            </article>
          </div>

          <div class="codex-troubleshooting">
            <h3>常见问题</h3>
            <dl><div><dt>401 Unauthorized</dt><dd>先确认 <code>base_url</code> 是本页显示的本站地址；本站当前为 <code>http://www.yucg.cn:8241/v1</code>，不要填写 <code>coloful-rose.com</code> 等内部上游地址。再确认 <code>experimental_bearer_token</code> 填写的是当前本站生成的完整 <code>sk-api-...</code> 密钥，且该密钥未撤销、未过期。旧站 Key、上游订阅 Token 和掩码文本均不可用。响应头 <code>X-Rose-Error-Source: local_auth</code> 表示请求已到本站但鉴权失败；没有该响应头且返回第三方错误，通常表示入口地址填错。</dd></div><div><dt>404 Not Found</dt><dd>确认 <code>base_url</code> 以一个 <code>/v1</code> 结尾，不要写成 <code>/v1/v1</code> 或追加 <code>/responses</code>。</dd></div><div><dt>429 Too Many Requests</dt><dd>查看 <code>X-Rose-Error-Source</code>：<code>local_rate_limit</code> 是本站单账号 RPM 上限，<code>local_queue</code> 是本站队列已满；其他情况通常来自上游。请按 <code>Retry-After</code> 稍后重试。账号正在处理另一个 Codex 长任务时会在本地排队，不再按旧逻辑于 30 秒后返回 429。</dd></div><div><dt>503 upstream_capacity</dt><dd>上游模型暂时满载。网关会在尚未输出正文或工具参数时自动切换账号并退避重试 2 次；重试耗尽才返回此错误。已经开始输出后不会重放，以免重复回答或重复工具调用。</dd></div><div><dt>503 local_queue_timeout</dt><dd>表示所有可用订阅账号持续繁忙。管理员可将 <code>ROSE_SUBSCRIPTION_QUEUE_TIMEOUT_SECONDS</code> 调大，或设为 <code>0</code> 一直等待；不要通过重复提交任务增加队列压力。即使无限等待，超过 <code>ROSE_SUBSCRIPTION_MAX_QUEUED_REQUESTS</code> 仍会立即拒绝，以保护服务。</dd></div><div><dt>模型不存在</dt><dd>复制模型广场里的真实模型 ID，或执行时使用 <code>-m</code> 覆盖默认模型。</dd></div><div><dt>配置无法识别</dt><dd>运行 <code>codex doctor</code> 和 <code>codex --strict-config</code> 查看诊断信息。</dd></div></dl>
          </div>

          <div class="docs-callout codex-callout"><b>安全</b><span><code>experimental_bearer_token</code> 会让密钥以明文保存在用户级配置中，请限制该文件的读取权限。远程接入请使用 HTTPS，不要把 API Key、Codex <code>auth.json</code> 或订阅账号 Token 发到聊天、截图、代码仓库中。</span></div>
          <p class="codex-sources">参考：<a href="https://learn.chatgpt.com/docs/windows/windows-app" target="_blank" rel="noopener">OpenAI Windows 安装说明</a> · <a href="https://developers.openai.com/codex/auth" target="_blank" rel="noopener">OpenAI Codex 身份验证</a> · <a href="https://learn.chatgpt.com/docs/config-file/config-reference" target="_blank" rel="noopener">Codex 配置参考</a> · <a href="https://developers.openai.com/codex/cli/reference" target="_blank" rel="noopener">Codex 命令参考</a></p>
        </section>
      </div>
    </div>
  </section>
</template>
<script>
import { copyToClipboard, notify } from '../ui'
import { buildCodexConfig, downloadTextFile } from '../config/codex'

export default {
  props: { appName: String, apiBaseUrl: String, codexConfig: Object, modelOptions: { type: Array, default: () => [] } },
  data () {
    return {
      baseUrl: this.apiBaseUrl || '',
      // Keep the tutorial source available for a future re-enable while it is
      // intentionally hidden from the current navigation and page.
      showTraeTutorial: false,
      openaiConfig: '',
      selectedCodexModel: '',
      codexModelIds: [],
      codexWindowsInstallText: '',
      codexInstallText: '',
      codexConfigText: '',
      apiKey: typeof window !== 'undefined' ? (window.sessionStorage.getItem('rose_fresh_api_key') || '') : '',
      codexOpenConfigCommand: '',
      codexRunText: '',
      codexModelCommand: '',
      copying: false
    }
  },
  created () { this.buildConfigs() },
  watch: { appName () { this.buildConfigs() }, apiBaseUrl (value) { if (value) { this.baseUrl = value.replace(/\/$/, ''); this.buildConfigs() } }, apiKey () { this.buildConfigs() }, codexConfig: { deep: true, handler () { this.buildConfigs() } }, modelOptions: { deep: true, handler () { this.buildConfigs() } } },
  methods: {
    buildConfigs () {
      const codex = this.codexConfig || {}
      const selectedModel = codex.model === 'gpt-5.6-sol' ? 'gpt-6-sol' : (codex.model || 'gpt-6-sol')
      const configuredSolModels = this.modelOptions.filter(item => String(item.id || '').startsWith('gpt-6-sol'))
      const models = configuredSolModels.length ? configuredSolModels : [{ id: selectedModel, reasoning_effort: codex.model_reasoning_effort }]
      const gptModels = this.modelOptions
        .map(item => String(item.id || '').trim())
        .filter((id, index, values) => id.startsWith('gpt-') && values.indexOf(id) === index)
      this.selectedCodexModel = selectedModel
      this.codexModelIds = [selectedModel, ...gptModels.filter(id => id !== selectedModel)].slice(0, 12)
      const modelLines = models.map(item => {
        const defaultEffort = item.reasoning_effort ? `默认 ${item.reasoning_effort}` : ''
        const levels = Array.isArray(item.reasoning_levels) && item.reasoning_levels.length ? `可选 ${item.reasoning_levels.join('/')}` : ''
        return `- ${item.id}${defaultEffort || levels ? `（${[defaultEffort, levels].filter(Boolean).join('，')}）` : ''}`
      }).join('\n')
      this.openaiConfig = `model_id: ${selectedModel}\nurl: ${this.baseUrl}/v1\nkey: <在 API 密钥页面创建后填入>\n\n可用 gpt-6-sol 档位：\n${modelLines}\n\nPOST ${this.baseUrl}/v1/chat/completions\nAuthorization: Bearer <key>`
      // Keep Codex as the final tutorial section. The gateway URL and provider
      // name come from public server configuration; no upstream key is exposed.
      this.codexWindowsInstallText = `winget install --id 9PLM9XGG6VKS -s msstore`
      this.codexInstallText = `node --version\nnpm install -g @openai/codex\ncodex --version`
      this.codexConfigText = buildCodexConfig({ appName: this.appName, apiBaseUrl: this.baseUrl, codexConfig: codex, apiKey: this.apiKey })
      this.codexOpenConfigCommand = `New-Item -ItemType Directory -Force "$env:USERPROFILE\\.codex" | Out-Null\nnotepad "$env:USERPROFILE\\.codex\\config.toml"`
      this.codexRunText = `cd "C:\\你的项目目录"\n\n# 打开交互界面\ncodex --strict-config -m "${selectedModel}"\n\n# 执行一次任务后退出\ncodex exec -m "${selectedModel}" "请只回复：Codex 接入成功"`
      this.codexModelCommand = `codex --strict-config -m "${selectedModel}" "请分析当前项目并给出三个改进建议"`
    },
    async copy (value) {
      const text = String(value || '')
      if (!text) {
        notify('暂无可复制内容', 'error')
        return
      }
      if (this.copying) return
      this.copying = true
      const copied = await copyToClipboard(text)
      notify(copied ? '已完整复制配置' : '复制失败，请检查浏览器权限', copied ? 'success' : 'error')
      this.copying = false
    },
    hasApiKey () {
      return String(this.apiKey || '').trim().length > 0
    },
    copyCodexConfig () {
      if (!this.hasApiKey()) {
        notify('本站 API Key 不能为空，不能复制会写入空密钥的配置', 'error')
        return
      }
      this.copy(this.codexConfigText)
    },
    downloadConfig () {
      if (!this.hasApiKey()) {
        notify('本站 API Key 不能为空，不能下载会写入空密钥的配置', 'error')
        return
      }
      downloadTextFile('config.toml', this.codexConfigText)
      notify('config.toml 已下载，密钥已填入', 'success')
    }
  }
}
</script>

<style scoped>
.trae-tutorial > p {
  max-width: 780px;
}

.trae-steps {
  display: grid;
  gap: 14px;
  margin-top: 22px;
}

.trae-step {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) minmax(210px, 320px);
  gap: 20px;
  align-items: start;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--card);
}

.trae-step-index {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--acid);
  color: var(--ink);
  font: 10px var(--mono);
  font-weight: 600;
}

.trae-step-copy {
  min-width: 0;
  padding-top: 2px;
}

.trae-step-copy h3 {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -.02em;
}

.trae-step-copy p {
  margin: 0;
  font-size: 12px;
  line-height: 1.75;
}

.trae-step-copy strong {
  color: var(--ink);
  font-weight: 600;
}

.trae-step figure {
  width: 100%;
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: #f0f2ef;
  text-align: center;
}

.trae-step figure img {
  display: block;
  width: 100%;
  max-height: 390px;
  object-fit: contain;
}

.trae-step figure figcaption {
  padding: 8px 10px 10px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 10px;
  line-height: 1.5;
  text-align: left;
}

.trae-inline-code {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-top: 14px;
  padding: 9px 10px;
  border: 1px solid var(--line);
  background: color-mix(in srgb, var(--paper) 72%, transparent);
  font-size: 10px;
}

.trae-inline-code span {
  color: var(--muted);
  white-space: nowrap;
}

.trae-inline-code code {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font: 10px var(--mono);
}

.trae-inline-code button {
  margin-left: auto;
  border: 0;
  background: transparent;
  color: var(--ink);
  font: 10px var(--mono);
  white-space: nowrap;
}

.trae-callout {
  margin-top: 18px;
}

.docs-content section {
  scroll-margin-top: 24px;
}

.codex-tutorial > p {
  max-width: 790px;
}

.codex-overview {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 22px 0 16px;
}

.codex-overview > div {
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--line);
  background: var(--card);
}

.codex-overview span,
.codex-overview small {
  display: block;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.5;
}

.codex-overview span {
  font-family: var(--mono);
  letter-spacing: .08em;
  text-transform: uppercase;
}

.codex-overview strong {
  display: block;
  overflow: hidden;
  margin: 9px 0 5px;
  font: 500 17px 'Playfair Display', Georgia, serif;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.codex-steps {
  display: grid;
  gap: 12px;
}

.codex-step {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  gap: 15px;
  padding: 19px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--card);
}

.codex-step-index {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: var(--ink);
  color: var(--acid);
  font: 10px var(--mono);
}

.codex-step-body {
  min-width: 0;
}

.codex-step-body h3,
.codex-troubleshooting h3 {
  margin: 2px 0 7px;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -.02em;
}

.codex-step-body p {
  margin: 0;
  font-size: 12px;
}

.codex-step-body .code-block {
  margin-top: 14px;
}

.codex-install-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 15px;
}

.codex-install-option {
  min-width: 0;
  padding: 15px;
  border: 1px solid var(--line);
  background: var(--paper);
}

.codex-install-option.featured {
  border-color: color-mix(in srgb, var(--acid) 70%, var(--line));
  box-shadow: inset 3px 0 0 var(--acid);
}

.codex-install-option-head {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 8px;
}

.codex-install-option-head span {
  padding: 4px 6px;
  background: var(--ink);
  color: var(--acid);
  font: 9px var(--mono);
  letter-spacing: .06em;
}

.codex-install-option-head b {
  font-size: 13px;
}

.codex-install-option p {
  min-height: 55px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.65;
}

.codex-install-option .code-block {
  margin-top: 12px;
}

.codex-link {
  min-height: 35px;
  margin-top: 13px;
  padding: 0 13px;
  font-size: 11px;
}

.codex-file-command {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px dashed var(--line);
}

.codex-config-paths {
  display: grid;
  gap: 10px;
  margin: 14px 0;
  padding: 0;
  list-style: none;
}

.codex-config-paths li {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--ink);
  font-size: 12px;
}

.codex-config-paths span {
  flex: 0 0 auto;
  font-weight: 600;
}

.codex-config-paths code {
  padding: 0;
  background: transparent;
  color: var(--ink);
  font: 11px var(--mono);
}

.codex-key-field {
  display: grid;
  gap: 7px;
  margin-top: 15px;
  padding: 13px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: color-mix(in srgb, var(--blue) 42%, var(--paper));
}

.codex-key-field label {
  color: var(--muted);
  font: 10px var(--mono);
  letter-spacing: .05em;
}

.codex-key-field input {
  width: 100%;
  min-height: 42px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--card);
  color: var(--ink);
  font: 11px var(--mono);
}

.codex-key-field small {
  color: var(--muted);
  font-size: 10px;
}

.code-head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.download-config-btn {
  color: color-mix(in srgb, var(--acid) 75%, var(--ink)) !important;
  font-weight: 600 !important;
}

.codex-file-command code {
  min-width: 0;
  overflow: hidden;
  font: 10px var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.codex-file-command button {
  flex: 0 0 auto;
  margin-left: auto;
  border: 0;
  background: transparent;
  color: var(--ink);
  font: 10px var(--mono);
}

.codex-model-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 13px;
}

.codex-model-list code {
  padding: 6px 8px;
  border: 1px solid var(--line);
  background: var(--paper);
  font: 10px var(--mono);
}

.codex-troubleshooting {
  margin-top: 16px;
  padding: 20px;
  border: 1px solid var(--line);
  background: color-mix(in srgb, var(--blue) 35%, var(--card));
}

.codex-troubleshooting dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 24px;
  margin: 13px 0 0;
}

.codex-troubleshooting dl > div {
  padding: 11px 0;
  border-top: 1px solid color-mix(in srgb, var(--line) 78%, transparent);
}

.codex-troubleshooting dt {
  font: 10px var(--mono);
  font-weight: 600;
}

.codex-troubleshooting dd {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.65;
}

.codex-callout {
  margin-top: 16px;
}

.codex-sources {
  font-size: 11px !important;
}

.codex-sources a {
  color: var(--ink);
  font-weight: 600;
}

:global(html[data-theme="dark"]) .trae-step {
  background: linear-gradient(145deg, #182735, #162330);
  border-color: #30485b;
}

:global(html[data-theme="dark"]) .trae-step-copy strong,
:global(html[data-theme="dark"]) .trae-inline-code button {
  color: #e0ebf3;
}

:global(html[data-theme="dark"]) .trae-step figure {
  background: #202b35;
  border-color: #3a5265;
}

:global(html[data-theme="dark"]) .trae-inline-code {
  background: #111d2a;
  border-color: #30485b;
}

:global(html[data-theme="dark"]) .codex-overview > div,
:global(html[data-theme="dark"]) .codex-step {
  background: linear-gradient(145deg, #182735, #162330);
  border-color: #30485b;
}

:global(html[data-theme="dark"]) .codex-step-index {
  background: var(--acid);
  color: #17212a;
}

:global(html[data-theme="dark"]) .codex-file-command,
:global(html[data-theme="dark"]) .codex-model-list code,
:global(html[data-theme="dark"]) .codex-install-option {
  background: #111d2a;
  border-color: #30485b;
}

:global(html[data-theme="dark"]) .codex-key-field {
  background: #132532;
  border-color: #30485b;
}

:global(html[data-theme="dark"]) .codex-config-paths code {
  background: transparent;
  color: #d8e5ed;
}

:global(html[data-theme="dark"]) .codex-key-field input {
  background: #0f1d28;
  border-color: #365064;
}

:global(html[data-theme="dark"]) .codex-install-option.featured {
  border-color: color-mix(in srgb, var(--acid) 65%, #30485b);
}

:global(html[data-theme="dark"]) .codex-file-command button,
:global(html[data-theme="dark"]) .codex-sources a {
  color: #d9e5ed;
}

:global(html[data-theme="dark"]) .codex-troubleshooting {
  background: #172a38;
  border-color: #30485b;
}

@media (max-width: 900px) {
  .trae-step {
    grid-template-columns: 34px minmax(0, 1fr);
  }

  .trae-step figure {
    grid-column: 2;
    max-width: 430px;
  }

  .codex-overview {
    grid-template-columns: 1fr;
  }

  .codex-install-options {
    grid-template-columns: 1fr;
  }

  .codex-install-option p {
    min-height: 0;
  }
}

@media (max-width: 560px) {
  .trae-step {
    gap: 12px;
    padding: 14px;
  }

  .trae-step-copy h3 {
    font-size: 15px;
  }

  .trae-step figure {
    max-width: none;
  }

  .trae-step figure img {
    max-height: 460px;
  }

  .trae-inline-code {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .trae-inline-code code {
    flex: 1 1 100%;
    order: 3;
  }

  .codex-step {
    grid-template-columns: 32px minmax(0, 1fr);
    gap: 11px;
    padding: 14px;
  }

  .codex-step-index {
    width: 28px;
    height: 28px;
  }

  .codex-troubleshooting dl {
    grid-template-columns: 1fr;
  }

  .codex-file-command {
    align-items: flex-start;
    flex-direction: column;
  }

  .codex-file-command code {
    width: 100%;
    white-space: normal;
    word-break: break-all;
  }

  .codex-file-command button {
    margin-left: 0;
  }

  .codex-config-paths li {
    align-items: flex-start;
    flex-direction: column;
    gap: 5px;
  }

  .codex-config-paths code {
    max-width: 100%;
    overflow-wrap: anywhere;
  }

  .code-head-actions {
    align-items: flex-start;
    flex-direction: column;
    gap: 5px;
  }
}
</style>
