from sqlalchemy import String, Text, DateTime, JSON, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from enum import Enum
from app.database import Base


class IssueStatus(str, Enum):
    analysis = "问题分析中"
    solving  = "问题解决中"
    verify   = "效果验证"
    closed   = "问题关闭"


class IssueModule(str, Enum):
    arm_control = "机械臂控制"
    perception  = "感知"
    hw_design   = "硬件设计"
    vendor_if   = "供应商接口"
    unknown     = "未分类"


class Issue(Base):
    __tablename__ = "issues"

    id:          Mapped[int]  = mapped_column(primary_key=True)
    jira_key:    Mapped[str]  = mapped_column(String(32), unique=True, index=True)  # ACMS-52
    title:       Mapped[str]  = mapped_column(String(512))
    description: Mapped[str | None]  = mapped_column(Text, nullable=True)
    status:      Mapped[IssueStatus] = mapped_column(SAEnum(IssueStatus), default=IssueStatus.analysis)
    module:      Mapped[IssueModule] = mapped_column(SAEnum(IssueModule), default=IssueModule.unknown)

    # 归档字段（由 AI 填写，人类确认后固化）
    root_cause:        Mapped[str | None] = mapped_column(Text, nullable=True)
    fix_solution:      Mapped[str | None] = mapped_column(Text, nullable=True)
    rejected_solutions:Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{plan, reason}]
    fix_code_location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fix_confirmed_by:  Mapped[str | None] = mapped_column(String(64), nullable=True)  # 飞书用户名
    verify_result:     Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_by:       Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 实体足迹（用于相似 issue 检测）
    entity_footprint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {"modules": [...], "components": [...], "vehicles": [...]}

    # AI 推断的影响范围（文本）
    impact_scope: Mapped[str | None] = mapped_column(Text, nullable=True)

    # AI 推断的 JIRA 字段（待人类确认后执行 transition）
    jira_fields_pending: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 相似 issue（由 AI 检测）
    similar_issue_keys: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["ACMS-50"]

    # 时间戳
    jira_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    jira_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
