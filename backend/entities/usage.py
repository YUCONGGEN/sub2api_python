from dataclasses import dataclass
from springbootai.orm import Column, CreateTime, Entity, Id, Index


@Entity(
    "usage_records",
    indexes=[
        Index("idx_usage_user_created", ["user_id", "created_at"]),
        Index("idx_usage_model_created", ["model", "created_at"]),
    ],
    comment="模型调用 token 与费用记录",
)
@dataclass
class UsageRecord:
    id: int = Id()
    user_id: int = Column(nullable=False)
    model: str = Column(nullable=False, length=160)
    prompt_tokens: int = Column(nullable=False, default=0)
    completion_tokens: int = Column(nullable=False, default=0)
    total_tokens: int = Column(nullable=False, default=0)
    cost: float = Column(nullable=False, default=0)
    status: str = Column(nullable=False, length=30, default="SUCCEEDED")
    created_at: str = CreateTime()


