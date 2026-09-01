from dataclasses import dataclass
from springbootai.orm import Column, CreateTime, Entity, Id, Index


@Entity(
    "api_keys",
    indexes=[Index("idx_api_keys_user", ["user_id", "created_at"])],
    comment="用户主动创建的 API 密钥",
)
@dataclass
class ApiKey:
    id: int = Id()
    user_id: int = Column(nullable=False)
    name: str = Column(nullable=False, length=64, default="未命名密钥")
    api_key: str = Column(nullable=False, unique=True, length=255)
    enabled: bool = Column(nullable=False, default=True)
    created_at: str = CreateTime()
    last_used: str = Column(nullable=True)
    expires_at: str = Column(nullable=True)


