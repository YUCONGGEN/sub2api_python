from dataclasses import dataclass

from springbootai.orm import Column, CreateTime, Entity, Id, Index


@Entity(
    "upstream_subscription_accounts",
    indexes=[
        Index("idx_upstream_subscription_provider", ["provider", "enabled", "priority"]),
        Index("idx_upstream_subscription_cooldown", ["provider", "cooldown_until"]),
        Index("idx_upstream_subscription_owner", ["owner_user_id", "created_at"]),
    ],
    comment="Claude/OpenAI subscription accounts managed by the gateway",
)
@dataclass
class UpstreamSubscriptionAccount:
    """Metadata only; OAuth credentials are encrypted before persistence."""

    id: int = Id()
    owner_user_id: int = Column(nullable=True)
    provider: str = Column(nullable=False, length=20)
    name: str = Column(nullable=False, length=120)
    auth_type: str = Column(nullable=False, length=30, default="oauth")
    email: str = Column(nullable=False, length=255, default="")
    account_ref: str = Column(nullable=False, length=255, default="")
    credentials_encrypted: str = Column(nullable=False)
    models_json: str = Column(nullable=False, default="[]")
    enabled: bool = Column(nullable=False, default=True)
    priority: int = Column(nullable=False, default=0)
    weight: int = Column(nullable=False, default=1)
    input_price_cny: float = Column(nullable=False, default=0)
    output_price_cny: float = Column(nullable=False, default=0)
    price_multiplier: float = Column(nullable=False, default=1)
    status: str = Column(nullable=False, length=30, default="READY")
    error_count: int = Column(nullable=False, default=0)
    last_error: str = Column(nullable=False, default="")
    expires_at: str = Column(nullable=True)
    cooldown_until: str = Column(nullable=True)
    last_used_at: str = Column(nullable=True)
    compliance_confirmed_at: str = Column(nullable=False)
    created_at: str = CreateTime()
    updated_at: str = Column(nullable=False)


__all__ = ["UpstreamSubscriptionAccount"]
