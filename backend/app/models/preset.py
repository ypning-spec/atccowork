from sqlalchemy import String, DateTime, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from app.database import Base
from app.models.user import UserRole


class UserRolePreset(Base):
    """Admin 预登记表：飞书名字 → 角色。用户登录时自动匹配，改表即时生效。"""
    __tablename__ = "user_role_presets"

    id:           Mapped[int]      = mapped_column(primary_key=True)
    feishu_name:  Mapped[str]      = mapped_column(String(64), unique=True, index=True)
    role:         Mapped[UserRole] = mapped_column(SAEnum(UserRole))
    note:         Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at:   Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at:   Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
