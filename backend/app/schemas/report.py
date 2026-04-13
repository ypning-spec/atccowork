from pydantic import BaseModel
from datetime import datetime
from app.models.report import ReportStatus


class ReportSessionOut(BaseModel):
    session_id: str
    status: ReportStatus
    draft_title: str | None = None
    draft_description: str | None = None
    draft_component: str | None = None
    draft_severity: str | None = None
    draft_steps: str | None = None
    draft_time_range: str | None = None
    jira_key: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReportChatIn(BaseModel):
    text: str


class ReportChatOut(BaseModel):
    speaker: str
    text: str
    draft: ReportSessionOut | None = None


class ReportConfirmOut(BaseModel):
    ok: bool
    jira_key: str
