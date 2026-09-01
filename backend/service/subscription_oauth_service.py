"""PKCE OAuth flows for authorized Claude and OpenAI subscription accounts."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from springbootai import PostConstruct, Service, get_config


OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_REDIRECT_URI = "http://localhost:1455/auth/callback"
OPENAI_SCOPES = "openid profile email offline_access"

CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_AUTHORIZE_URL = "https://claude.com/cai/oauth/authorize"
CLAUDE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
CLAUDE_SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _utc_after(seconds: int | float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, float(seconds)))).isoformat()


@Service("subscription_oauth_service")
class SubscriptionOAuthService:
    """Keep short-lived PKCE verifiers in memory and exchange only once."""

    @PostConstruct
    def init(self) -> None:
        cfg = get_config().get("rose", {}).get("subscription-gateway", {})
        self.timeout = max(5.0, min(180.0, float(cfg.get("oauth-timeout-seconds", 60) or 60)))
        self.trust_env = str(cfg.get("trust-env", False)).strip().lower() in {"1", "true", "yes", "on"}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _provider(provider: str) -> str:
        value = str(provider or "").strip().lower()
        if value not in {"openai", "claude"}:
            raise ValueError("provider 只能是 openai 或 claude")
        return value

    def generate_authorization(self, provider: str, admin_id: int) -> dict[str, str]:
        provider = self._provider(provider)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        session_id = secrets.token_urlsafe(24)
        created = time.time()
        with self._lock:
            self._sessions = {
                key: value for key, value in self._sessions.items()
                if created - float(value.get("created_at", 0)) < 1800
            }
            self._sessions[session_id] = {
                "provider": provider,
                "admin_id": int(admin_id),
                "state": state,
                "verifier": verifier,
                "created_at": created,
            }
        if provider == "openai":
            query = {
                "response_type": "code",
                "client_id": OPENAI_CLIENT_ID,
                "redirect_uri": OPENAI_REDIRECT_URI,
                "scope": OPENAI_SCOPES,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "id_token_add_organizations": "true",
                "codex_cli_simplified_flow": "true",
            }
            authorize_url = OPENAI_AUTHORIZE_URL + "?" + urlencode(query)
        else:
            query = {
                "code": "true",
                "client_id": CLAUDE_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": CLAUDE_REDIRECT_URI,
                "scope": CLAUDE_SCOPES,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
            authorize_url = CLAUDE_AUTHORIZE_URL + "?" + urlencode(query)
        return {"provider": provider, "authorization_url": authorize_url, "session_id": session_id}

    def _consume_session(self, session_id: str, provider: str, admin_id: int, state: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(str(session_id))
            if not session or time.time() - float(session.get("created_at", 0)) >= 1800:
                raise ValueError("OAuth 会话不存在或已过期，请重新生成授权链接")
            if session.get("provider") != provider or int(session.get("admin_id", 0)) != int(admin_id):
                raise ValueError("OAuth 会话与当前管理员或供应商不匹配")
            if not state or not secrets.compare_digest(str(state), str(session.get("state") or "")):
                raise ValueError("OAuth state 校验失败")
            return dict(session)

    @staticmethod
    def _parse_code(provider: str, callback_value: str, explicit_state: str) -> tuple[str, str]:
        raw = str(callback_value or "").strip()
        state = str(explicit_state or "").strip()
        if not raw:
            raise ValueError("请粘贴授权回调地址或授权码")
        if "://" in raw:
            parsed = urlparse(raw)
            values = parse_qs(parsed.query)
            code = str((values.get("code") or [""])[0]).strip()
            state = state or str((values.get("state") or [""])[0]).strip()
        elif "#" in raw:
            code, returned_state = raw.split("#", 1)
            code = code.strip()
            state = state or returned_state.strip()
        else:
            code = raw
        if not code:
            raise ValueError("授权结果中没有 code")
        return code, state

    async def exchange(self, provider: str, *, session_id: str, callback_value: str, state: str, admin_id: int) -> dict[str, Any]:
        provider = self._provider(provider)
        code, returned_state = self._parse_code(provider, callback_value, state)
        session = self._consume_session(session_id, provider, admin_id, returned_state)
        if provider == "openai":
            form = {
                "grant_type": "authorization_code",
                "client_id": OPENAI_CLIENT_ID,
                "code": code,
                "redirect_uri": OPENAI_REDIRECT_URI,
                "code_verifier": session["verifier"],
            }
            response = await self._post_token(OPENAI_TOKEN_URL, data=form, headers={"originator": "codex-tui", "User-Agent": "codex-tui/0.146.0"})
        else:
            body = {
                "grant_type": "authorization_code",
                "client_id": CLAUDE_CLIENT_ID,
                "code": code,
                "redirect_uri": CLAUDE_REDIRECT_URI,
                "code_verifier": session["verifier"],
                "state": returned_state,
            }
            response = await self._post_token(CLAUDE_TOKEN_URL, json_body=body, headers={"User-Agent": "axios/1.13.6"})
        credentials = self._normalize_token(provider, response)
        with self._lock:
            self._sessions.pop(str(session_id), None)
        return credentials

    async def refresh(self, provider: str, refresh_token: str) -> dict[str, Any]:
        provider = self._provider(provider)
        if not str(refresh_token or "").strip():
            raise ValueError("账号没有 refresh_token，需要重新授权")
        if provider == "openai":
            response = await self._post_token(
                OPENAI_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": OPENAI_CLIENT_ID,
                    "scope": "openid profile email",
                },
                headers={"originator": "codex-tui", "User-Agent": "codex-tui/0.146.0"},
            )
        else:
            response = await self._post_token(
                CLAUDE_TOKEN_URL,
                json_body={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLAUDE_CLIENT_ID},
                headers={"User-Agent": "axios/1.13.6"},
            )
        result = self._normalize_token(provider, response)
        if not result.get("refresh_token"):
            result["refresh_token"] = refresh_token
        return result

    async def _post_token(self, url: str, *, data: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=self.trust_env, follow_redirects=False) as client:
            response = await client.post(url, data=data, json=json_body, headers={"Accept": "application/json", **(headers or {})})
        if response.status_code < 200 or response.status_code >= 300:
            detail = ""
            try:
                payload = response.json()
                error = payload.get("error") if isinstance(payload, dict) else None
                detail = str(error.get("message") if isinstance(error, dict) else error or payload)[:500]
            except (ValueError, TypeError):
                detail = response.text[:500]
            raise ValueError(f"OAuth token 交换失败（HTTP {response.status_code}）：{detail or '上游未返回详情'}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("OAuth 上游没有返回 JSON") from exc
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise ValueError("OAuth 上游没有返回 access_token")
        return payload

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict[str, Any]:
        try:
            part = str(token).split(".")[1]
            part += "=" * (-len(part) % 4)
            value = json.loads(base64.urlsafe_b64decode(part.encode("ascii")).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (IndexError, ValueError, UnicodeError, TypeError):
            return {}

    def _normalize_token(self, provider: str, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "access_token": str(payload.get("access_token") or ""),
            "refresh_token": str(payload.get("refresh_token") or ""),
            "token_type": str(payload.get("token_type") or "Bearer"),
            "scope": str(payload.get("scope") or ""),
        }
        expires_in = int(payload.get("expires_in") or 0)
        if expires_in > 0:
            result["expires_at"] = _utc_after(expires_in)
        if provider == "openai":
            id_token = str(payload.get("id_token") or "")
            if id_token:
                result["id_token"] = id_token
            claims = self._decode_jwt_payload(id_token or result["access_token"])
            auth = claims.get("https://api.openai.com/auth") if isinstance(claims, dict) else {}
            auth = auth if isinstance(auth, dict) else {}
            result.update({
                "email": str(claims.get("email") or ""),
                "account_id": str(auth.get("chatgpt_account_id") or auth.get("poid") or ""),
                "plan_type": str(auth.get("chatgpt_plan_type") or ""),
                "client_id": OPENAI_CLIENT_ID,
            })
        else:
            account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
            organization = payload.get("organization") if isinstance(payload.get("organization"), dict) else {}
            result.update({
                "email": str(account.get("email_address") or ""),
                "account_id": str(account.get("uuid") or ""),
                "organization_id": str(organization.get("uuid") or ""),
                "client_id": CLAUDE_CLIENT_ID,
            })
        return result


__all__ = ["SubscriptionOAuthService"]
