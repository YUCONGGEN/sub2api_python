function o(r){return String(r??"").replace(/\\/g,"\\\\").replace(/"/g,'\\"').replace(/[\r\n]+/g," ")}function _({appName:r,apiBaseUrl:n,codexConfig:s,apiKey:t}){const e=s||{},l=e.model||"gpt-5.6-sol",a=String(e.model_provider||"rose").replace(/[^a-zA-Z0-9_-]/g,"")||"rose",d=e.provider_name||r||"大模型接口管理",i=String(e.provider_base_url||n||"").replace(/\/$/,""),c=/\/v1$/i.test(i)?i:`${i}/v1`,p=String(t||"").trim()||"<粘贴本站 API Key>";return`model = "${o(l)}"
model_provider = "${o(a)}"
model_reasoning_effort = "${o(e.model_reasoning_effort||"high")}"
approval_policy = "${o(e.approval_policy||"on-request")}"
sandbox_mode = "${o(e.sandbox_mode||"workspace-write")}"

[model_providers.${a}]
name = "${o(d)}"
base_url = "${o(c)}"
experimental_bearer_token = "${o(p)}"
wire_api = "${o(e.wire_api||"responses")}"
requires_openai_auth = false
supports_websockets = ${e.supports_websockets===!0}`}function m(r,n){const s=new Blob([String(n||"")],{type:"text/plain;charset=utf-8"}),t=URL.createObjectURL(s),e=document.createElement("a");e.href=t,e.download=r,document.body.appendChild(e),e.click(),e.remove(),window.setTimeout(()=>URL.revokeObjectURL(t),0)}export{_ as b,m as d};
