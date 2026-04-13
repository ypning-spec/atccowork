from sqlalchemy import String, Text, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from enum import Enum
from app.database import Base


class RelationType(str, Enum):
    similar_symptom = "similar_symptom"   # 症状相似（AI 检测）
    same_component  = "same_component"    # 共享组件（实体重叠）
    same_root_cause = "same_root_cause"   # 确认同根因（人工确认）
    referenced      = "referenced"        # 分析时明确引用


class IssueRelation(Base):
    __tablename__ = "issue_relations"

    id:            Mapped[int] = mapped_column(primary_key=True)
    source_key:    Mapped[str] = mapped_column(String(32), index=True)  # ACMS-52
    target_key:    Mapped[str] = mapped_column(String(32), index=True)  # ACMS-50
    relation_type: Mapped[RelationType] = mapped_column(SAEnum(RelationType))
    confidence:    Mapped[float | None] = mapped_column(default=None)   # AI 置信度
    note:          Mapped[str | None]   = mapped_column(Text, nullable=True)
    confirmed:     Mapped[bool]         = mapped_column(default=False)  # 人工确认
