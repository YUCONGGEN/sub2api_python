import math

from springbootai.annotations import Autowired, GetMapping, PostMapping, RequestBody, RequestHeader, RequestMapping, RestController, RequestParam, PathVariable
from springbootai import get_config

from backend.service.auth_service import AuthService
from backend.service.ai_service import AiGatewayService
from backend.service.payment_service import PaymentConfigurationError, PaymentProviderError, PaymentService
from backend.common.response import bad, not_found, ok, unauthorized


@RestController
@RequestMapping("/api/billing")
class BillingController:
    @Autowired
    def __init__(self, auth: AuthService, payment: PaymentService, gateway: AiGatewayService):
        self.auth = auth
        self.store = self.auth.store
        self.payment = payment
        self.gateway = gateway

    @GetMapping("/plans")
    def plans(self, page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        cfg = get_config().get("rose", {}).get("billing", {})
        listener = self.payment.personal_listener_status()
        subscription_plans = self.store.list_subscription_plans(page, page_size, enabled_only=True)
        return ok({
            "ok": True,
            "currency": cfg.get("currency", "CNY"),
            "min_recharge": float(cfg.get("min-recharge", 10)),
            "demo_mode": self.payment.demo_mode(),
            "personal_wechat_available": listener["available"],
            "personal_wechat_status": listener,
            "official_tokens": 1_000_000,
            "models": [
                {**model, "unit": "CNY / 1M tokens"}
                for model in self.gateway.catalog()
                if model.get("enabled", True)
            ],
            "recharge_options": [10, 30, 100, 300, 1000],
            "subscription_plans": subscription_plans["items"],
            "subscription_plans_pagination": subscription_plans,
        })

    @GetMapping("/subscription-plans")
    def subscription_plans(self, page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        result = self.store.list_subscription_plans(page, page_size, enabled_only=True)
        return ok({"ok": True, "plans": result["items"], "pagination": result})

    @GetMapping("/subscriptions")
    def subscriptions(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        result = self.store.list_user_subscriptions(user["id"], page, page_size)
        return ok({"ok": True, "subscriptions": result["items"], "pagination": result})

    @GetMapping("/entitlements")
    def entitlements(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        result = self.store.list_user_entitlements(user["id"], page, page_size)
        return ok({"ok": True, "entitlements": result["items"], "pagination": result})

    @PostMapping("/subscriptions")
    def subscribe(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        try:
            plan_id = int(body.get("plan_id"))
            subscription = self.store.subscribe_plan(user["id"], plan_id)
        except (TypeError, ValueError) as exc:
            return bad(str(exc) or "套餐参数不正确", 409)
        if not subscription:
            return bad("套餐不存在或已下架", 404)
        return ok({"ok": True, "subscription": subscription}, "套餐订阅成功")

    @PostMapping("/subscriptions/{subscription_id}/cancel")
    def cancel_subscription(self, subscription_id: int = PathVariable(name="subscription_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        cancelled = self.store.cancel_user_subscription(user["id"], subscription_id)
        return ok({"ok": True}, "已关闭自动续订，当前套餐到期前仍可使用") if cancelled else bad("套餐不存在或已结束", 404)

    @PostMapping("/subscriptions/{subscription_id}/renew")
    def renew_subscription(self, subscription_id: int = PathVariable(name="subscription_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        try:
            subscription = self.store.renew_subscription(user["id"], subscription_id)
        except (TypeError, ValueError, RuntimeError) as exc:
            return bad(str(exc) or "套餐续订失败", 409)
        if not subscription:
            return bad("套餐不存在或已结束", 404)
        return ok({"ok": True, "subscription": subscription}, "套餐已续订，结束时间已顺延")

    @PostMapping("/subscriptions/{subscription_id}/auto-renew")
    def set_auto_renew(self, subscription_id: int = PathVariable(name="subscription_id"), body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        enabled = body.get("enabled", False)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in {"1", "true", "yes", "on"}
        try:
            subscription = self.store.set_subscription_auto_renew(user["id"], subscription_id, bool(enabled))
        except ValueError as exc:
            return bad(str(exc), 409)
        if not subscription:
            return bad("套餐不存在、已到期或已停用", 409)
        return ok({"ok": True, "subscription": subscription}, "自动续订已开启" if enabled else "自动续订已关闭")

    @GetMapping("/quotas")
    def quotas(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        result = self.store.list_user_quotas(user["id"], page, page_size)
        return ok({"ok": True, "quotas": result["items"], "pagination": result})

    @GetMapping("/orders")
    def orders(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        result = self.store.list_orders(user["id"], page, page_size)
        return ok({"ok": True, "orders": result["items"], "pagination": result})

    @PostMapping("/orders")
    def create_order(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        try:
            amount = float(body.get("amount", 0))
        except (TypeError, ValueError):
            return bad("充值金额必须是数字")
        provider = str(body.get("provider", "WECHAT")).upper()
        minimum = float(get_config().get("rose", {}).get("billing", {}).get("min-recharge", 10))
        if not math.isfinite(amount) or provider not in {"WECHAT", "WECHAT_PERSONAL", "ALIPAY"} or amount < minimum:
            return bad(f"请选择支付渠道，最低充值 {minimum:g} 元")
        amount = round(amount, 2)
        if provider == "WECHAT_PERSONAL":
            listener = self.payment.personal_listener_status()
            if not listener["available"]:
                return bad("微信收款窗口检测失效，暂时无法创建充值订单", 503)
            candidates = self.payment.personal_wechat_candidates(amount)
            if not candidates:
                return bad("该金额暂未配置个人微信收款码，请联系管理员", 503)
            order = self.store.create_personal_order(user["id"], amount, candidates)
            if not order:
                return bad("该金额的收款码正在使用，请稍后重试或换一个充值金额", 409)
        else:
            order = self.store.create_order(user["id"], provider, amount)
        try:
            checkout = self.payment.create_checkout(order)
            order = self.store.update_order_qr(order["trade_no"], checkout.get("qr_code") or order.get("qr_code")) or order
            return ok({"ok": True, "order": order, "checkout": checkout, "demo": self.payment.demo_mode()})
        except (PaymentConfigurationError, PaymentProviderError) as exc:
            return bad(str(exc), 503)

    @PostMapping("/orders/{trade_no}/pay")
    def pay_demo(self, trade_no: str = PathVariable(name="trade_no"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        if not self.payment.demo_mode():
            return bad("真实支付订单请等待微信/支付宝回调到账", 409)
        order = self.store.pay_order(trade_no, user["id"])
        if not order:
            return not_found("订单不存在")
        return ok({"ok": True, "order": order}, "演示支付已完成，余额已到账")

    @PostMapping("/orders/{trade_no}/cancel")
    def cancel_order(self, trade_no: str = PathVariable(name="trade_no"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        order = self.store.cancel_order(trade_no, user["id"])
        if not order:
            return not_found("订单不存在")
        if order["status"] == "PAID":
            return bad("订单已支付，不能取消", 409)
        return ok({"ok": True, "order": order}, "订单已取消，收款后不会自动入账")

    @PostMapping("/redeem-code")
    def redeem_code(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        code = str(body.get("code", "")).strip()
        if len(code) < 8 or len(code) > 128:
            return bad("兑换码格式不正确")
        result = self.store.redeem_recharge_code(code, user["id"])
        if not result:
            return bad("兑换码不存在、已使用或已撤销", 409)
        return ok({"ok": True, "order": result}, f"已到账 ¥{result['amount']:.2f}")




