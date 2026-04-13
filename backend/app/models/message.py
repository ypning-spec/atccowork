from sqlalchemy import String, Text, DateTime, Float, Enum as SAEnum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from enum import Enum
from app.database import Base


class MessageSource(str, Enum):
    jira_comment = "jira_comment"
    feishu_chat  = "feishu_chat"
    system       = "system"      # AI 主动输出
    user_chat    = "user_chat"   # 用户在本系统 chat 里的回复


class MessageType(str, Enum):
    # AI 分类标签
    hypothesis  = "hypothesis"   # 假设
    decision    = "decision"     # 决策
    data        = "data"         # 数据/测量结果
    action      = "action"       # 操作记录（如"已推代码"）
    question    = "question"     # 未回答的问题
    noise       = "noise"        # 无信息量
    unclassified = "unclassified"


class Message(Base):
    __tablename__ = "messages"

    id:         Mapped[int] = mapped_column(primary_key=True)
    issue_key:  Mapped[str] = mapped_column(String(32), index=True)  # ACMS-52
    source:     Mapped[MessageSource] = mapped_column(SAEnum(MessageSource))
    msg_type:   Mapped[MessageType]   = mapped_column(
        SAEnum(MessageType), default=MessageType.unclassified
    )
    raw_text:   Mapped[str] = mapped_column(Text)        # 原文，永远不改
    speaker_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    speaker_feishu_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # AI 分类置信度
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 原始来源 ID（JIRA comment id、飞书消息 id 等）
    source_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # 对话归档路径（写入哪个 markdown 文件）
    archive_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    timestamp:  Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
