from dataclasses import dataclass
from springbootai.orm import Column, CreateTime, Entity, Id, Index


@Entity(
    "payment_orders",
    indexes=[
        Index("idx_orders_user_created", ["user_id", "created_at"]),
        Index("idx_orders_payment_amount", ["provider", "payment_amount", "status"]),
    ],
    comment="充值订单",
)
@dataclass
class PaymentOrder:
    id: int = Id()
    user_id: int = Column(nullable=False)
    provider: str = Column(nullable=False, length=40)
    trade_no: str = Column(nullable=False, unique=True, length=100)
    amount: float = Column(nullable=False)
    credits: float = Column(nullable=False)
    status: str = Column(nullable=False, length=30, default="PENDING")
    qr_code: str = Column(nullable=True, column_definition="TEXT")
    created_at: str = CreateTime()
    paid_at: str = Column(nullable=True)
    payment_amount: float = Column(nullable=True)
    qr_asset: str = Column(nullable=True, length=255)


@Entity(
    "recharge_codes",
    indexes=[Index("idx_recharge_codes_status", ["status"])],
    comment="管理员生成的充值兑换码",
)
@dataclass
class RechargeCode:
    id: int = Id()
    code_hash: str = Column(nullable=False, unique=True, length=128)
    amount: float = Column(nullable=False)
    created_by: int = Column(nullable=False)
    created_at: str = CreateTime()
    redeemed_by: int = Column(nullable=True)
    redeemed_at: str = Column(nullable=True)
    status: str = Column(nullable=False, length=20, default="ACTIVE")
    code: str = Column(nullable=True, length=128)
    expires_at: str = Column(nullable=True)


@Entity(
    "payment_callbacks",
    indexes=[Index("idx_payment_callback_hash", ["callback_hash"], unique=True)],
    comment="支付回调幂等记录",
)
@dataclass
class PaymentCallback:
    id: int = Id()
    callback_hash: str = Column(nullable=False, unique=True, length=128)
    provider: str = Column(nullable=False, length=40)
    payment_amount: float = Column(nullable=False)
    trade_no: str = Column(nullable=True, length=100)
    received_at: str = CreateTime()


@Entity("payment_listener_status", comment="个人微信监听器状态")
@dataclass
class PaymentListenerStatus:
    id: int = Id()
    available: bool = Column(nullable=False, default=False)
    reason: str = Column(nullable=False, length=500, default="")
    updated_at: str = Column(nullable=False)
    last_alert_at: str = Column(nullable=True)


