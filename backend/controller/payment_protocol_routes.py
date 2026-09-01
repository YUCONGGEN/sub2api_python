"""Raw provider notification adapters kept outside Result controllers."""

from backend.protocol.payment_adapter import (
    alipay_notify,
    personal_wechat_callback,
    personal_wechat_qr,
    wechat_notify,
)


def register_payment_protocol_routes(app):
    """Register signed form/JSON and binary QR endpoints unchanged."""
    app.add_api_route("/api/payment/alipay/notify", alipay_notify, methods=["POST"], tags=["Payment Notifications"])
    app.add_api_route("/api/payment/wechat/notify", wechat_notify, methods=["POST"], tags=["Payment Notifications"])
    app.add_api_route("/api/payment/personal-wechat/callback", personal_wechat_callback, methods=["POST"], tags=["Payment Notifications"])
    app.add_api_route("/api/payment/personal-wechat/qr/{filename}", personal_wechat_qr, methods=["GET"], tags=["Payment Notifications"])


__all__ = ["register_payment_protocol_routes"]
