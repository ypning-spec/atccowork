from sqlalchemy import String, Text, DateTime, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from enum import Enum
from app.database import Base


class ReportStatus(str, Enum):
    drafting  = "drafting"    # 对话中，待确认
    confirmed = "confirmed"   # 已确认，JIRA 已创建
    cancelled = "cancelled"   # 已取消


class IssueReport(Base):
    __tablename__ = "issue_reports"

    id:            Mapped[int] = mapped_column(primary_key=True)
    session_id:    Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 前端 UUID
    reporter_uid:  Mapped[str] = mapped_column(String(128))
    reporter_name: Mapped[str] = mapped_column(String(64))
    status:        Mapped[ReportStatus] = mapped_column(
        SAEnum(ReportStatus), default=ReportStatus.drafting
    )

    # AI 填充的 JIRA 草稿字段（每轮对话后刷新）
    draft_title:       Mapped[str | None] = mapped_column(String(512), nullable=True)
    draft_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_component:   Mapped[str | None] = mapped_column(String(128), nullable=True)
    draft_severity:    Mapped[str | None] = mapped_column(String(16), nullable=True)
    draft_steps:       Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_time_range:  Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 确认提报后填入
    jira_key: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
