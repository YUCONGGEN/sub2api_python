"""SpringBootAI application controllers.

Raw OpenAI, payment-notification, and QR response adapters are registered by
the controller modules but live in ``backend.protocol`` so this package keeps
the application layer annotation-first.
"""

from .payment_controller import PaymentController, register_payment_routes
from .proxy_controller import ProxyController, register_proxy_route
from .subscription_admin_controller import SubscriptionAdminController

__all__ = [
    "PaymentController",
    "ProxyController",
    "SubscriptionAdminController",
    "register_payment_routes",
    "register_proxy_route",
]
