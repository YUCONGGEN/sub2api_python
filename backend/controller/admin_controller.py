from springbootai import get_config
import csv
import io
import secrets
import string
import re
import math

from springbootai.annotations import Autowired, DeleteMapping, GetMapping, PatchMapping, PostMapping, RequestBody, RequestHeader, RequestMapping, RestController, RequestParam, PathVariable

from backend.service.auth_service import AuthService
from backend.service.conversation_service import ConversationService
from backend.service.observability_service import ObservabilityService
from backend.common.response import as_bool, bad, forbidden, not_found, ok


@RestController
@RequestMapping("/api/admin")
class AdminController:
    @Autowired
    def __init__(self, auth: AuthService, conversations: ConversationService, observability_service: ObservabilityService):
        self.auth = auth
        self.store = self.auth.store
        self.conversations = conversations
        self.observability = observability_service

    def admin(self, authorization: str | None):
        user = self.auth.user_from_authorization(authorization)
        return user if user and user.get("role") == "ADMIN" else None

    @GetMapping("/summary")
    def summary(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        summary = self.store.admin_summary()
        summary["conversation_totals"] = self.conversations.totals()
        recent = self.conversations.list_page(None, 1, 5)
        summary["recent_conversations"] = recent.get("items", [])
        summary["recent_conversations_pagination"] = {
            key: recent.get(key) for key in ("total", "page", "page_size", "pages")
        }
        summary.update(self.observability.dashboard())
        return ok({"ok": True, "summary": summary})

    @GetMapping("/logs")
    def logs(self, authorization: str = RequestHeader(name="Authorization", required=False), level: str = RequestParam(name="level", required=False, default=""), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        """Return paginated warning/error records without request bodies."""
        if not self.admin(authorization):
            return forbidden()
        result = self.observability.logs_page(page, page_size, level)
        return ok({"ok": True, "logs": result["items"], "pagination": result})

    @GetMapping("/usage-export")
    def usage_export(self, authorization: str = RequestHeader(name="Authorization", required=False), start_at: str = RequestParam(name="start_at", required=False, default=""), end_at: str = RequestParam(name="end_at", required=False, default=""), user_id: str = RequestParam(name="user_id", required=False, default=""), model: str = RequestParam(name="model", required=False, default=""), status: str = RequestParam(name="status", required=False, default="")):
        """Generate a database-independent, metadata-only CSV export."""
        if not self.admin(authorization):
            return forbidden()
        rows = self.store.export_usage(start_at=start_at, end_at=end_at, user_id=user_id, model=model, status=status)
        columns = ["id", "user_id", "username", "request_id", "protocol", "model", "status", "prompt_tokens", "completion_tokens", "total_tokens", "cost", "created_at", "completed_at", "latency_ms"]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return ok({"ok": True, "filename": "usage-export.csv", "csv": "\ufeff" + output.getvalue(), "count": len(rows)})

    @GetMapping("/subscription-plans")
    def subscription_plans(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        if not self.admin(authorization):
            return forbidden()
        result = self.store.list_subscription_plans(page, page_size, enabled_only=False)
        return ok({"ok": True, "plans": result["items"], "pagination": result})

    @PostMapping("/subscription-plans")
    def create_subscription_plan(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        try:
            plan = self.store.create_subscription_plan(body)
        except (TypeError, ValueError) as exc:
            return bad(str(exc) or "套餐参数不正确")
        except Exception:
            return bad("套餐名称已存在", 409)
        return ok({"ok": True, "plan": plan}, "套餐已创建")

    @PatchMapping("/subscription-plans/{plan_id}")
    def update_subscription_plan(self, plan_id: int, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        try:
            plan = self.store.update_subscription_plan(plan_id, body)
        except (TypeError, ValueError) as exc:
            return bad(str(exc) or "套餐参数不正确")
        if not plan:
            return not_found("套餐不存在")
        return ok({"ok": True, "plan": plan}, "套餐已更新")

    @DeleteMapping("/subscription-plans/{plan_id}")
    def delete_subscription_plan(self, plan_id: int, authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        deleted = self.store.delete_subscription_plan(plan_id)
        return ok({"ok": True}, "套餐已停用，历史订阅继续保留") if deleted else not_found("套餐不存在")

    @GetMapping("/users")
    def users(self, authorization: str = RequestHeader(name="Authorization", required=False), keyword: str = RequestParam(name="keyword", required=False, default=""), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        if not self.admin(authorization):
            return forbidden()
        result = self.store.list_users(keyword, page, page_size)
        return ok({"ok": True, "users": result["items"], "pagination": result})

    @GetMapping("/user-groups/{group_id}")
    def user_group_detail(self, group_id: int = PathVariable(name="group_id"), authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        if not self.admin(authorization):
            return forbidden()
        detail = self.store.admin_group_detail(group_id, page, page_size)
        if detail is None:
            return not_found("分组不存在或已删除")
        return ok({"ok": True, **detail})

    @GetMapping("/user-groups")
    def user_groups(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        if not self.admin(authorization):
            return forbidden()
        result = self.store.list_user_groups(page, page_size)
        return ok({"ok": True, "groups": result["items"], "pagination": result})

    @PostMapping("/user-groups")
    def create_user_group(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        try:
            group = self.store.create_user_group(body)
        except (TypeError, ValueError) as exc:
            return bad(str(exc) or "用户组参数不正确")
        except Exception:
            return bad("用户组名称已存在", 409)
        return ok({"ok": True, "group": group}, "用户组已创建")

    @PatchMapping("/user-groups/{group_id}")
    def update_user_group(self, group_id: int = PathVariable(name="group_id"), body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        try:
            group = self.store.update_user_group(group_id, body)
        except (TypeError, ValueError) as exc:
            return bad(str(exc) or "用户组参数不正确")
        except Exception:
            return bad("用户组名称已存在", 409)
        return ok({"ok": True, "group": group}, "用户组已更新") if group else not_found("用户组不存在")

    @DeleteMapping("/user-groups/{group_id}")
    def delete_user_group(self, group_id: int = PathVariable(name="group_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        try:
            deleted = self.store.delete_user_group(group_id)
        except ValueError as exc:
            return bad(str(exc), 409)
        return ok({"ok": True}, "用户组已删除，原成员已转入默认组") if deleted else not_found("用户组不存在")

    @PostMapping("/users")
    def create_user(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        """Create an account and reveal its first API key exactly once."""
        if not self.admin(authorization):
            return forbidden()
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        email = str(body.get("email", "")).strip()
        role = str(body.get("role", "USER")).upper().strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            return bad("账户名需为 3-32 位字母、数字、下划线、中划线或点")
        if len(password) < 6 or len(password) > 128:
            return bad("密码长度需为 6-128 位")
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return bad("邮箱格式不正确")
        if role not in {"USER", "ADMIN"}:
            return bad("角色不正确")
        try:
            balance = float(body.get("balance", 0))
        except (TypeError, ValueError):
            return bad("初始余额必须是数字")
        if not math.isfinite(balance) or balance < -0.1 or balance > 1_000_000_000:
            return bad("初始余额必须在 -0.1 到 1000000000 元之间")
        try:
            group_id = int(body["group_id"]) if body.get("group_id") not in (None, "") else None
            user, default_key = self.store.create_user_with_default_key(
                username,
                password,
                email,
                role,
                balance=balance,
                enabled=as_bool(body.get("enabled"), True),
                group_id=group_id,
            )
        except ValueError as exc:
            return bad(str(exc) or "用户组不正确")
        except Exception:
            return bad("创建失败，账户名可能已存在", 409)
        return ok({
            "ok": True,
            "user": self.store.public_user(user),
            "api_key": default_key.get("api_key"),
            "api_key_id": default_key.get("id"),
        })

    @PatchMapping("/users/{user_id}")
    def update_user(self, user_id: int = PathVariable(name="user_id"), body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        changes = {}
        if "balance" in body:
            try:
                changes["balance"] = float(body["balance"])
            except (TypeError, ValueError):
                return bad("余额必须是数字")
            if not math.isfinite(changes["balance"]) or changes["balance"] < -0.1 or changes["balance"] > 1_000_000_000:
                return bad("余额必须在 -0.1 到 1000000000 元之间")
        if "enabled" in body:
            changes["enabled"] = 1 if as_bool(body["enabled"]) else 0
        if "role" in body and body["role"] in {"ADMIN", "USER"}:
            changes["role"] = body["role"]
        if "email" in body:
            email = str(body["email"]).strip()
            if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                return bad("邮箱格式不正确")
            changes["email"] = email
        if "password" in body:
            password = str(body["password"])
            if password:
                if len(password) < 6 or len(password) > 128:
                    return bad("密码长度需为 6-128 位")
                changes["password"] = password
        if "group_id" in body:
            changes["group_id"] = body.get("group_id")
        try:
            user = self.store.update_user(user_id, **changes)
        except ValueError as exc:
            return bad(str(exc) or "用户参数不正确")
        if not user:
            return not_found("用户不存在")
        return ok({"ok": True, "user": user})

    @GetMapping("/users/{user_id}")
    def user_detail(self, user_id: int = PathVariable(name="user_id"), authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        if not self.admin(authorization):
            return forbidden()
        detail = self.store.admin_user_detail(user_id, page, page_size)
        if not detail:
            return not_found("用户不存在")
        return ok({"ok": True, **detail})

    @GetMapping("/users/{user_id}/usage")
    def user_usage(self, user_id: int = PathVariable(name="user_id"), authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        if not self.admin(authorization):
            return forbidden()
        if not self.store.find_user(user_id):
            return not_found("用户不存在")
        result = self.store.list_usage_page(user_id, page, page_size)
        return ok({"ok": True, "usage": result["items"], "pagination": result})

    @GetMapping("/users/{user_id}/quotas")
    def user_quotas(self, user_id: int = PathVariable(name="user_id"), authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        if not self.admin(authorization):
            return forbidden()
        if not self.store.find_user(user_id):
            return not_found("用户不存在")
        result = self.store.list_user_quotas(user_id, page, page_size)
        return ok({"ok": True, "quotas": result["items"], "pagination": result})

    @PostMapping("/users/{user_id}/quotas")
    def create_user_quota(self, user_id: int = PathVariable(name="user_id"), body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        if not self.store.find_user(user_id):
            return not_found("用户不存在")
        try:
            quota = self.store.create_user_quota(user_id, body)
        except (TypeError, ValueError) as exc:
            return bad(str(exc) or "额度参数不正确")
        return ok({"ok": True, "quota": quota}, "免费额度已创建")

    @PatchMapping("/users/{user_id}/quotas/{quota_id}")
    def update_user_quota(self, user_id: int = PathVariable(name="user_id"), quota_id: int = PathVariable(name="quota_id"), body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        try:
            quota = self.store.update_user_quota(user_id, quota_id, body)
        except (TypeError, ValueError) as exc:
            return bad(str(exc) or "额度参数不正确")
        if not quota:
            return not_found("额度策略不存在")
        return ok({"ok": True, "quota": quota}, "免费额度已更新")

    @DeleteMapping("/users/{user_id}/quotas/{quota_id}")
    def delete_user_quota(self, user_id: int = PathVariable(name="user_id"), quota_id: int = PathVariable(name="quota_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        deleted = self.store.delete_user_quota(user_id, quota_id)
        return ok({"ok": True}, "免费额度已删除") if deleted else not_found("额度策略不存在")

    @GetMapping("/users/{user_id}/subscriptions")
    def user_subscriptions(self, user_id: int = PathVariable(name="user_id"), authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        if not self.admin(authorization):
            return forbidden()
        if not self.store.find_user(user_id):
            return not_found("用户不存在")
        result = self.store.list_user_subscriptions(user_id, page, page_size)
        return ok({"ok": True, "subscriptions": result["items"], "pagination": result})

    @PatchMapping("/users/{user_id}/subscriptions/{subscription_id}")
    def update_user_subscription(self, user_id: int = PathVariable(name="user_id"), subscription_id: int = PathVariable(name="subscription_id"), body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        try:
            subscription = self.store.update_user_subscription(user_id, subscription_id, body)
        except (TypeError, ValueError) as exc:
            return bad(str(exc) or "套餐参数不正确")
        if not subscription:
            return not_found("用户套餐不存在")
        return ok({"ok": True, "subscription": subscription}, "用户套餐已更新")

    @DeleteMapping("/users/{user_id}/subscriptions/{subscription_id}")
    def delete_user_subscription(self, user_id: int = PathVariable(name="user_id"), subscription_id: int = PathVariable(name="subscription_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        deleted = self.store.delete_user_subscription(user_id, subscription_id)
        return ok({"ok": True}, "用户套餐已删除") if deleted else not_found("用户套餐不存在")

    @DeleteMapping("/users/{user_id}")
    def delete_user(self, user_id: int = PathVariable(name="user_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        admin = self.admin(authorization)
        if not admin:
            return forbidden()
        if int(admin["id"]) == int(user_id):
            return bad("不能删除当前登录的管理员账户", 409)
        deleted = self.store.delete_user(user_id)
        return ok({"ok": True}, "账户已停用并匿名化，历史账务记录已保留") if deleted else not_found("用户不存在")

    @PostMapping("/recharge-codes")
    def create_recharge_codes(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        admin = self.admin(authorization)
        if not admin:
            return forbidden()
        try:
            amount = round(float(body.get("amount", 0)), 2)
            count = int(body.get("count", 1))
            expire_hours = int(body.get("expire_hours", 48))
        except (TypeError, ValueError):
            return bad("金额、数量和有效期必须是数字")
        if not math.isfinite(amount) or amount <= 0 or amount > 1_000_000 or count < 1 or count > 500:
            return bad("金额或生成数量超出范围")
        if expire_hours < 1 or expire_hours > 24 * 365:
            return bad("兑换码有效期必须在 1 小时到 365 天之间")
        alphabet = string.ascii_uppercase + string.digits
        billing = get_config().get("rose", {}).get("billing", {})
        configured_prefix = str(billing.get("recharge-code-prefix", "CODE")).strip().upper()
        prefix = re.sub(r"[^A-Z0-9]+", "", configured_prefix) or "CODE"
        result = []
        for _ in range(count):
            code = prefix + "-" + "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
            try:
                row = self.store.create_recharge_code(amount, admin["id"], code, expire_hours)
            except Exception:
                continue
            row["code"] = code
            result.append(row)
        if not result:
            return bad("兑换码生成失败，请稍后重试")
        return ok({"ok": True, "codes": result, "expire_hours": expire_hours})

    @GetMapping("/recharge-codes")
    def recharge_codes(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        if not self.admin(authorization):
            return forbidden()
        result = self.store.list_recharge_codes(page, page_size)
        return ok({"ok": True, "codes": result["items"], "pagination": result})

    @PostMapping("/recharge-codes/{code_id}/revoke")
    def revoke_recharge_code(self, code_id: int = PathVariable(name="code_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self.admin(authorization):
            return forbidden()
        revoked = self.store.revoke_recharge_code(code_id)
        return ok({"ok": True}, "兑换码已撤销") if revoked else not_found("兑换码不存在或已使用")


