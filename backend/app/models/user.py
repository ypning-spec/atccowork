from sqlalchemy import String, DateTime, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from enum import Enum
from app.database import Base


class UserRole(str, Enum):
    admin    = "admin"    # 管理员 · 驾驶舱 + 所有节点 + 用户管理
    pm       = "pm"       # 项目经理 · 仅驾驶舱（总览）
    dev      = "dev"      # 研发   · 问题分析 + 问题解决
    verify   = "verify"   # 验证   · 效果验证 + 关闭确认
    readonly = "readonly" # 只读   · 仅查看


class User(Base):
    __tablename__ = "users"

    id:           Mapped[int]  = mapped_column(primary_key=True)
    feishu_uid:   Mapped[str]  = mapped_column(String(128), unique=True, index=True)
    feishu_name:  Mapped[str]  = mapped_column(String(64))
    feishu_avatar:Mapped[str | None] = mapped_column(String(512), nullable=True)
    role:         Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.readonly)
    is_active:    Mapped[bool] = mapped_column(default=True)
    created_at:   Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
