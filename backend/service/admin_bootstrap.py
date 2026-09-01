"""Idempotent application-account bootstrap.

The repository owns schema creation; this component owns the policy that the
configured administrator exists. Keeping the two concerns separate follows
the SpringBootAI component/service split and makes startup safe to repeat.
"""

from springbootai import Autowired, PostConstruct, Service, get_config

from backend.service.store_service import StoreService


@Service("admin_bootstrap")
class AdminBootstrap:
    """Create the configured administrator on first startup only."""

    @Autowired
    def __init__(self, store: StoreService):
        self.store = store

    @PostConstruct
    def ensure_admin(self) -> None:
        config = get_config().get("rose", {}).get("bootstrap", {})
        username = str(config.get("admin-username", "")).strip()
        password = str(config.get("admin-password", ""))
        if not username or not password or self.store.find_by_username(username):
            return
        try:
            balance = float(config.get("initial-balance", 0) or 0)
        except (TypeError, ValueError):
            balance = 0
        self.store.create_user(
            username=username,
            password=password,
            email=str(config.get("admin-email", "")).strip(),
            role="ADMIN",
            balance=balance,
            enabled=True,
        )


__all__ = ["AdminBootstrap"]
