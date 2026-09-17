from springbootai import get_config
import re
from urllib.parse import urlsplit, urlunsplit

from springbootai.annotations import GetMapping, RequestMapping, RestController
from backend.common.response import ok


@RestController
@RequestMapping("/api/config")
class ConfigController:
    """Public, non-secret runtime settings needed to bootstrap the frontend."""

    @staticmethod
    def _public_base_url(value: object) -> str:
        """Return a browser/client-ready gateway root with an explicit scheme.

        Older YAML files used ``host:port`` without ``http://``. Browsers treat
        that as a relative path, which made the UI render only ``/v1`` or
        generated invalid client configuration. Normalize both legacy and new
        values at the API boundary while keeping ``/v1`` out of the root.
        """
        raw = str(value or "").strip()
        if not raw:
            raw = "http://www.yucg.cn:8241"
        if "://" not in raw:
            raw = "http://" + raw.lstrip("/")
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower() or "http"
        netloc = parsed.netloc or parsed.path.split("/", 1)[0]
        path = parsed.path if parsed.netloc else ("/" + parsed.path.split("/", 1)[1] if "/" in parsed.path else "")
        path = re.sub(r"/v1/?$", "", path.rstrip("/"))
        return urlunsplit((scheme, netloc, path, "", "")).rstrip("/")

    @GetMapping("/public")
    def public_config(self):
        cfg = get_config().get("rose", {})
        application = cfg.get("application", {}) if isinstance(cfg.get("application"), dict) else {}
        api_cfg = cfg.get("api", {}) if isinstance(cfg.get("api"), dict) else {}
        billing = cfg.get("billing", {}) if isinstance(cfg.get("billing"), dict) else {}
        name = str(application.get("name") or "大模型接口管理").strip() or "大模型接口管理"
        base_url = self._public_base_url(api_cfg.get("public-base-url"))
        # Keep the public placeholder in lockstep with the generator: only the
        # ASCII code alphabet is accepted, so a configured value can never
        # produce a placeholder that cannot be generated.
        prefix = str(billing.get("recharge-code-prefix") or "CODE").strip().upper()
        prefix = re.sub(r"[^A-Z0-9]+", "", prefix) or "CODE"
        codex = cfg.get("codex", {}) if isinstance(cfg.get("codex"), dict) else {}
        providers = codex.get("providers", {}) if isinstance(codex.get("providers"), dict) else {}
        rose_provider = providers.get("rose", {}) if isinstance(providers.get("rose"), dict) else {}
        models = cfg.get("models", [])
        subscription_gateway = cfg.get("subscription-gateway", {}) if isinstance(cfg.get("subscription-gateway"), dict) else {}
        contributions_enabled = str(subscription_gateway.get("user-contributions-enabled", True)).strip().lower() in {"1", "true", "yes", "on"}
        config_request_default_use_proxy = str(subscription_gateway.get("config-request-default-use-proxy", False)).strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(models, dict):
            models = [{"id": key, **(value if isinstance(value, dict) else {})} for key, value in models.items()]
        model_options = []
        for item in models if isinstance(models, list) else []:
            if not isinstance(item, dict) or not item.get("id") or item.get("enabled", True) is False:
                continue
            model_options.append({
                "id": str(item["id"]),
                "reasoning_effort": item.get("reasoning-effort"),
                "reasoning_levels": list(item.get("reasoning-levels") or []),
                "description": item.get("description", ""),
            })
        return ok({
            "ok": True,
            "name": name,
            "api_base_url": base_url,
            "recharge_code_prefix": prefix,
            "recharge_code_placeholder": f"输入 {prefix}-XXXX-XXXX-XXXX",
            "subscription_contributions_enabled": contributions_enabled,
            "subscription_config_request_default_use_proxy": config_request_default_use_proxy,
            # Public metadata only. The bearer token is intentionally omitted.
            "codex": {
                "model": codex.get("model") or "gpt-5.6-sol",
                "model_provider": codex.get("model-provider") or "rose",
                "model_reasoning_effort": codex.get("model-reasoning-effort") or "high",
                "approval_policy": codex.get("approval-policy") or "on-request",
                "sandbox_mode": codex.get("sandbox-mode") or "workspace-write",
                "disable_response_storage": bool(codex.get("disable-response-storage", True)),
                # The public tutorial must point at this gateway, never the
                # private upstream provider URL from YAML.
                "provider_name": name,
                "provider_base_url": base_url,
                "wire_api": rose_provider.get("wire-api") or "responses",
                "supports_websockets": bool(rose_provider.get("supports-websockets", False)),
            },
            "models": model_options,
        })


