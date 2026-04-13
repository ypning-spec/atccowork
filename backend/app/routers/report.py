"""
问题提报 API — 测试人员通过对话方式提报新问题
POST /report/session              → 创建提报会话
POST /report/{session_id}/chat    → 发消息，AI 回复并更新草稿
GET  /report/{session_id}         → 获取草稿内容
POST /report/{session_id}/confirm → 确认提报 → 创建 JIRA → 触发分析
DELETE /report/{session_id}       → 取消提报
"""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.database import get_db, SessionLocal
from app.models.report import IssueReport, ReportStatus
from app.models.issue import Issue, IssueStatus
from app.models.user import User
from app.schemas.report import ReportSessionOut, ReportChatIn, ReportChatOut, ReportConfirmOut
from app.services import ai, jira as jira_svc, log_service
from app.services.workflow import trigger_analysis
from app.routers.auth import get_current_user

router = APIRouter(prefix="/report", tags=["report"])
logger = logging.getLogger(__name__)

# 每个 session 的对话历史（内存缓存，仅在进程内有效）
_session_history: dict[str, list[dict]] = {}

WELCOME_MESSAGE = (
    "你好！请描述你发现的问题，包括：\n\n"
    "1. **发生时间**（尽量精确到小时）\n"
    "2. **问题现象**（看到了什么异常）\n"
    "3. **发生位置**（车位编号、车型等）\n\n"
    "我会帮你整理提报信息，完成后点击「确认提报」即可自动写入 JIRA。"
)


@router.post("/session", response_model=ReportSessionOut)
async def create_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建新的提报会话"""
    session_id = str(uuid.uuid4())
    report = IssueReport(
        session_id=session_id,
        reporter_uid=current_user.feishu_uid,
        reporter_name=current_user.feishu_name,
        status=ReportStatus.drafting,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    _session_history[session_id] = []
    return report


@router.get("/{session_id}", response_model=ReportSessionOut)
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    report = db.query(IssueReport).filter(IssueReport.session_id == session_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Session not found")
    return report


@router.post("/{session_id}/chat", response_model=ReportChatOut)
async def report_chat(
    session_id: str,
    body: ReportChatIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    用户发消息：
    1. 尝试拉取日志（stub）
    2. AI 回复 + 更新草稿字段
    3. 持久化草稿到 DB
    """
    report = db.query(IssueReport).filter(IssueReport.session_id == session_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Session not found")
    if report.status != ReportStatus.drafting:
        raise HTTPException(status_code=400, detail="Session is already confirmed or cancelled")

    history = _session_history.get(session_id, [])

    # 尝试拉取日志：首条消息用空时间占位；后续消息若已提取到 time_range 则传入
    log_summary = ""
    try:
        time_range = report.draft_time_range or ""
        # Parse "YYYY-MM-DD HH:MM ~ HH:MM" or similar into start/end
        if " ~ " in time_range:
            parts = time_range.split(" ~ ", 1)
            start_time = parts[0].strip()
            end_time = parts[1].strip()
        else:
            start_time = time_range
            end_time = ""
        log_summary = await log_service.fetch_logs(
            start_time=start_time,
            end_time=end_time,
            keywords=[body.text[:50]],
        )
    except Exception as e:
        logger.warning("fetch_logs failed: %s", e)

    # AI 填充草稿字段
    reply_text, draft = await ai.fill_report_fields(
        conversation_history=history,
        user_text=body.text,
        log_summary=log_summary,
    )

    # 更新对话历史
    history.append({"role": "user",      "content": body.text})
    history.append({"role": "assistant", "content": reply_text})
    _session_history[session_id] = history

    # 持久化草稿字段到 DB
    if draft.get("title"):       report.draft_title       = draft["title"]
    if draft.get("description"): report.draft_description = draft["description"]
    if draft.get("component"):   report.draft_component   = draft["component"]
    if draft.get("severity"):    report.draft_severity    = draft["severity"]
    if draft.get("steps_to_reproduce"): report.draft_steps = draft["steps_to_reproduce"]
    if draft.get("time_range"):  report.draft_time_range  = draft["time_range"]
    db.commit()
    db.refresh(report)

    return ReportChatOut(
        speaker="boringbot",
        text=reply_text,
        draft=ReportSessionOut.model_validate(report),
    )


@router.post("/{session_id}/confirm", response_model=ReportConfirmOut)
async def confirm_report(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    确认提报：
    1. 在 JIRA 创建新 issue
    2. 在本地 DB upsert Issue 记录（status=analysis）
    3. 触发 AI 分析
    """
    report = db.query(IssueReport).filter(IssueReport.session_id == session_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Session not found")
    if report.status != ReportStatus.drafting:
        raise HTTPException(status_code=400, detail="Session already confirmed or cancelled")
    if not report.draft_title:
        raise HTTPException(status_code=400, detail="请先描述问题，草稿标题为空")

    # 构建 JIRA description
    desc_parts = [report.draft_description or ""]
    if report.draft_steps:
        desc_parts.append(f"\n\n**复现步骤：**\n{report.draft_steps}")
    if report.draft_time_range:
        desc_parts.append(f"\n\n**故障时间：** {report.draft_time_range}")
    desc_parts.append(f"\n\n*由 {current_user.feishu_name} 通过 ACMS 提报系统提交*")
    full_description = "".join(desc_parts)

    # 创建 JIRA issue
    try:
        jira_key = await jira_svc.create_issue(
            summary=report.draft_title,
            description=full_description,
            component=report.draft_component,
        )
    except Exception as e:
        logger.error("JIRA create_issue failed: %s", e)
        raise HTTPException(status_code=502, detail=f"JIRA 创建失败：{e}")

    # 本地 DB 写入 Issue
    new_issue = Issue(
        jira_key=jira_key,
        title=report.draft_title,
        description=full_description,
        status=IssueStatus.analysis,
    )
    db.add(new_issue)
    db.flush()

    # 更新 report 状态
    report.status   = ReportStatus.confirmed
    report.jira_key = jira_key
    db.commit()
    db.refresh(new_issue)

    # 清理内存对话历史
    _session_history.pop(session_id, None)

    # 后台触发 AI 分析
    _bg(_trigger_analysis_bg, new_issue.id)

    logger.info("Issue reported: %s by %s", jira_key, current_user.feishu_name)
    return ReportConfirmOut(ok=True, jira_key=jira_key)


@router.delete("/{session_id}")
def cancel_session(
    session_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    report = db.query(IssueReport).filter(IssueReport.session_id == session_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Session not found")
    report.status = ReportStatus.cancelled
    db.commit()
    _session_history.pop(session_id, None)
    return {"ok": True}


# ── helpers ───────────────────────────────────────────────────────────────────

def _bg(fn, *args):
    import asyncio
    try:
        asyncio.get_event_loop().create_task(fn(*args))
    except RuntimeError:
        pass


async def _trigger_analysis_bg(issue_id: int):
    db = SessionLocal()
    try:
        issue = db.query(Issue).filter(Issue.id == issue_id).first()
        if issue:
            await trigger_analysis(issue, db)
    except Exception as e:
        logger.warning("Background trigger_analysis failed for issue_id=%d: %s", issue_id, e)
    finally:
        db.close()
