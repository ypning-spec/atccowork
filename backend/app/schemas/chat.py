from pydantic import BaseModel
from datetime import datetime


class ChatMessageIn(BaseModel):
    text: str


class ChatMessageOut(BaseModel):
    speaker: str
    text: str
    timestamp: datetime
    msg_type: str | None = None


class ChatHistoryOut(BaseModel):
    issue_key: str
    stage: str
    messages: list[ChatMessageOut]
    raw_markdown: str
