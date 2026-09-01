"""Public payment notification endpoints.

These endpoints intentionally bypass the normal JSON controller wrapper because
Alipay sends form-urlencoded notifications while WeChat sends signed JSON.
"""

from __future__ import annotations

import json
import hashlib
import io
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from backend.service.payment_service import PaymentConfigurationError, PaymentProviderError, PaymentService


def _service(request: Request) -> PaymentService:
    context = request.app.state.spring_application.application_context
    return context.get_bean("payment_service")


async def alipay_notify(request: Request):
    service = _service(request)
    body = await request.body()
    values = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    params = {key: items[-1] if items else "" for key, items in values.items()}
    try:
        if not service.demo_mode() and not service.verify_alipay_notify(params):
            return PlainTextResponse("fail")
    except (PaymentConfigurationError, ValueError, TypeError):
        return PlainTextResponse("fail")
    if params.get("trade_status") not in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
        return PlainTextResponse("success")
    trade_no = params.get("out_trade_no")
    if not trade_no:
        return PlainTextResponse("fail")
    order = service.store.find_order(trade_no)
    if not order or order.get("provider") != "ALIPAY":
        return PlainTextResponse("fail")
    try:
        if abs(float(params.get("total_amount", "nan")) - float(order["amount"])) > 0.01:
            return PlainTextResponse("fail")
    except (TypeError, ValueError):
        return PlainTextResponse("fail")
    order = service.store.pay_order(trade_no)
    return PlainTextResponse("success" if order else "fail")


async def wechat_notify(request: Request):
    service = _service(request)
    body = await request.body()
    try:
        if not service.demo_mode() and not service.verify_wechat_notify(dict(request.headers), body):
            return JSONResponse({"code": "FAIL", "message": "签名验证失败"}, status_code=401)
        payload = json.loads(body.decode("utf-8"))
        resource = service.decrypt_wechat_resource(payload["resource"])
    except (json.JSONDecodeError, KeyError, PaymentConfigurationError, PaymentProviderError, ValueError, TypeError):
        return JSONResponse({"code": "FAIL", "message": "通知解析失败"}, status_code=400)
    if resource.get("trade_state") != "SUCCESS":
        return JSONResponse({"code": "SUCCESS", "message": "成功"})
    trade_no = str(resource.get("out_trade_no", ""))
    order = service.store.find_order(trade_no)
    if not order or order.get("provider") != "WECHAT":
        return JSONResponse({"code": "FAIL", "message": "订单不存在或渠道不匹配"}, status_code=404)
    amount = (resource.get("amount") or {}).get("total")
    try:
        if abs(float(amount) / 100 - float(order["amount"])) > 0.01:
            return JSONResponse({"code": "FAIL", "message": "订单金额不匹配"}, status_code=400)
    except (TypeError, ValueError):
        return JSONResponse({"code": "FAIL", "message": "订单金额缺失"}, status_code=400)
    order = service.store.pay_order(trade_no)
    if not order:
        return JSONResponse({"code": "FAIL", "message": "订单不存在"}, status_code=404)
    return JSONResponse({"code": "SUCCESS", "message": "成功"})


async def personal_wechat_callback(request: Request):
    service = _service(request)
    expected_token = str(service.personal_wechat_config().get("callback-token") or "").strip()
    if expected_token and request.headers.get("X-Personal-Callback-Token", "") != expected_token:
        return JSONResponse({"ok": False, "message": "回调鉴权失败"}, status_code=401)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "回调必须是 JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "message": "回调格式错误"}, status_code=400)
    if str(payload.get("type", "")).strip().lower() == "listener_status":
        available = bool(payload.get("available"))
        status = service.record_personal_listener_status(available, str(payload.get("reason", "")))
        return JSONResponse({"ok": True, "status": status})
    raw_amount = payload.get("amount", payload.get("amountAll"))
    try:
        amount = round(float(raw_amount), 2)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "message": "缺少有效收款金额"}, status_code=400)
    event_id = str(payload.get("eventId") or payload.get("transactionId") or "").strip()
    if event_id:
        callback_hash = hashlib.sha256(("event:" + event_id).encode()).hexdigest()
    else:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        callback_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    result = service.store.settle_personal_callback(amount, callback_hash, payload)
    if result["status"] == "PAID":
        return JSONResponse({"ok": True, "status": "PAID", "trade_no": result["trade_no"], "amount": result["amount"]})
    if result["status"] == "DUPLICATE":
        return JSONResponse({"ok": True, "status": "DUPLICATE", "trade_no": result.get("trade_no")})
    if result["status"] == "AMBIGUOUS":
        return JSONResponse({"ok": False, "status": "AMBIGUOUS", "message": "收款金额对应多个待支付订单，已拒绝自动入账"}, status_code=409)
    return JSONResponse({"ok": False, "status": "NOT_FOUND", "message": "没有匹配的待支付订单"}, status_code=404)


async def personal_wechat_status(request: Request):
    service = _service(request)
    return JSONResponse({"ok": True, "status": service.personal_listener_status()})


def _personal_qr_roots(service: PaymentService) -> list[Path]:
    cfg = service.personal_wechat_config()
    roots = []
    for raw in (cfg.get("asset-dir", "./wexin_pay"), cfg.get("fallback-asset-dir", "./weixin_pay")):
        root = Path(str(raw)).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        roots.append(root.resolve())
    return roots


async def personal_wechat_qr(filename: str, request: Request):
    service = _service(request)
    safe_name = Path(filename).name
    if safe_name != filename or Path(filename).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return JSONResponse({"ok": False, "message": "二维码文件名无效"}, status_code=400)
    if not service.store.has_qr_asset(safe_name):
        return JSONResponse({"ok": False, "message": "二维码未被订单选中"}, status_code=404)
    source = next((root / safe_name for root in _personal_qr_roots(service) if (root / safe_name).is_file()), None)
    if source is None:
        return JSONResponse({"ok": False, "message": "二维码文件不存在"}, status_code=404)
    try:
        from PIL import Image
        cfg = service.personal_wechat_config().get("qr-crop", {})
        with Image.open(source) as image:
            width, height = image.size
            left = max(0, min(width - 1, int(cfg.get("left", width * 0.27))))
            top = max(0, min(height - 1, int(cfg.get("top", height * 0.31))))
            right = max(left + 1, min(width, int(cfg.get("right", width * 0.73))))
            bottom = max(top + 1, min(height, int(cfg.get("bottom", height * 0.66))))
            cropped = image.convert("RGB").crop((left, top, right, bottom))
            output = io.BytesIO()
            cropped.save(output, format="PNG", optimize=True)
        return Response(output.getvalue(), media_type="image/png", headers={"Cache-Control": "public, max-age=300"})
    except ImportError:
        return JSONResponse({"ok": False, "message": "服务端缺少 Pillow 依赖"}, status_code=503)
    except Exception:
        return JSONResponse({"ok": False, "message": "二维码处理失败"}, status_code=500)


def register_payment_routes(app):
    app.add_api_route("/api/payment/alipay/notify", alipay_notify, methods=["POST"], tags=["Payment Notifications"])
    app.add_api_route("/api/payment/wechat/notify", wechat_notify, methods=["POST"], tags=["Payment Notifications"])
    app.add_api_route("/api/payment/personal-wechat/callback", personal_wechat_callback, methods=["POST"], tags=["Payment Notifications"])
    app.add_api_route("/api/payment/personal-wechat/qr/{filename}", personal_wechat_qr, methods=["GET"], tags=["Payment Notifications"])


