"""SpringBootAI application controllers.

Raw OpenAI, payment-notification, and QR response adapters are registered by
the controller modules but live in ``backend.protocol`` so this package keeps
the application layer annotation-first.
"""

from .payment_controller import PaymentController, register_payment_routes
from .proxy_controller import ProxyController, register_proxy_route
from .admin_config_controller import AdminConfigController
from .subscription_admin_controller import SubscriptionAdminController
from .subscription_controller import SubscriptionController
from .proxy_pool_admin_controller import ProxyPoolAdminController
from .admin_model_catalog_controller import AdminModelCatalogController

__all__ = [
    "PaymentController",
    "ProxyController",
    "AdminConfigController",
    "SubscriptionAdminController",
    "SubscriptionController",
    "ProxyPoolAdminController",
    "AdminModelCatalogController",
    "register_payment_routes",
    "register_proxy_route",
]
