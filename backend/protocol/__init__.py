"""Raw-wire adapters for protocols that cannot use Result-wrapped controllers."""

from .openai_adapter import openai_chat, openai_models, openai_responses
from .payment_adapter import alipay_notify, personal_wechat_callback, personal_wechat_qr, wechat_notify

__all__ = [
    "openai_chat",
    "openai_models",
    "openai_responses",
    "alipay_notify",
    "wechat_notify",
    "personal_wechat_callback",
    "personal_wechat_qr",
]
