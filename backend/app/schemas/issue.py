from pydantic import BaseModel
from datetime import datetime
from typing import Any
from app.models.issue import IssueStatus, IssueModule


class IssueListItem(BaseModel):
    id: int
    jira_key: str
    title: str
    status: IssueStatus
    module: IssueModule
    root_cause: str | None = None   # 用于驾驶舱统计"知识已归档"数量
    updated_at: datetime

    model_config = {"from_attributes": True}


class IssueOut(BaseModel):
    id: int
    jira_key: str
    title: str
    description: str | None
    status: IssueStatus
    module: IssueModule
    root_cause: str | None
    fix_solution: str | None
    fix_code_location: str | None
    rejected_solutions: list[Any] | None
    impact_scope: str | None
    fix_confirmed_by: str | None
    verify_result: str | None
    verified_by: str | None
    similar_issue_keys: list[str] | None
    jira_fields_pending: dict | None
    jira_created_at: datetime | None
    jira_updated_at: datetime | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConfirmAnalysisIn(BaseModel):
    """人类确认 AI 的根因分析结果，可选修改"""
    root_cause: str | None = None       # None 表示不修改
    fix_solution: str | None = None
    comment: str = ""                   # 额外备注写入对话归档


class ConfirmVerifyIn(BaseModel):
    """验证结论确认"""
    result: str                         # e.g. "压测通过，未复现"
    comment: str = ""
