from dataclasses import dataclass
from springbootai.orm import Column, CreateTime, Entity, Id, Index


@Entity(
    "users",
    indexes=[Index("idx_users_username", ["username"], unique=True)],
    comment="代理系统用户账户",
)
@dataclass
class User:
    id: int = Id()
    username: str = Column(nullable=False, unique=True, length=64)
    password_hash: str = Column(nullable=False, length=255)
    email: str = Column(nullable=False, length=255, default="")
    role: str = Column(nullable=False, length=20, default="USER")
    balance: float = Column(nullable=False, default=0)
    enabled: bool = Column(nullable=False, default=True)
    api_key: str = Column(nullable=False, unique=True, length=255)
    created_at: str = CreateTime()
    last_login: str = Column(nullable=True)


