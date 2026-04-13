"""
JIRA & GitLab Webhook 接收器
JIRA: POST /webhooks/jira
GitLab: POST /webhooks/gitlab（预留）
"""
import hashlib, hmac, logging
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.config import get_settings
from app.models.issue import Issue, IssueStatus
from app.models.message import Message, MessageSource
from app.services import ai, conversation_store
from app.services.workflow import trigger_analysis, trigger_solving, trigger_verify
from datetime import datetime, timezone

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
settings = get_settings()
logger = logging.getLogger(__name__)

STATUS_MAP = {
    "问题分析中": IssueStatus.analysis,
    "问题解决中": IssueStatus.solving,
    "效果验证":   IssueStatus.verify,
    "问题关闭":   IssueStatus.closed,
}


def _verify_jira_signature(body: bytes, signature: str) -> bool:
    if not settings.jira_webhook_secret:
        return True  # 开发模式跳过验证
    expected = hmac.new(
        settings.jira_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


@router.post("/jira")
async def jira_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_jira_signature(body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event   = payload.get("webhookEvent", "")
    logger.info("JIRA webhook: %s", event)

    if event in ("jira:issue_updated", "jira:issue_created"):
        await _handle_issue_event(payload, db)
    elif event == "comment_created":
        await _handle_comment(payload, db)

    return {"ok": True}


async def _handle_issue_event(payload: dict, db: Session):
    issue_data  = payload.get("issue", {})
    fields      = issue_data.get("fields", {})
    jira_key    = issue_data.get("key", "")
    title       = fields.get("summary", "")
    status_name = fields.get("status", {}).get("name", "")
    new_status  = STATUS_MAP.get(status_name)

    if not jira_key or not new_status:
        return

    issue = db.query(Issue).filter(Issue.jira_key == jira_key).first()
    is_new = issue is None
    if is_new:
        issue = Issue(jira_key=jira_key, title=title)
        db.add(issue)
        db.flush()

    prev_status = issue.status
    issue.title  = title
    issue.status = new_status
    db.commit()

    # ── 节点1：进入「问题分析中」→ AI 自动根因分析 ─────────────────
    if new_status == IssueStatus.analysis and (is_new or prev_status != IssueStatus.analysis):
        await trigger_analysis(issue, db)

    # ── 节点2：进入「问题解决中」→ AI 自动生成修复简报 ─────────────
    elif new_status == IssueStatus.solving and prev_status != IssueStatus.solving:
        await trigger_solving(issue, db)

    # ── 节点3：进入「效果验证」→ AI 自动生成验证清单 ───────────────
    elif new_status == IssueStatus.verify and prev_status != IssueStatus.verify:
        await trigger_verify(issue, db)


async def _handle_comment(payload: dict, db: Session):
    """新评论：分类归档"""
    comment    = payload.get("comment", {})
    issue_key  = payload.get("issue", {}).get("key", "")
    raw_text   = comment.get("body", "")
    author     = comment.get("author", {}).get("displayName", "")
    comment_id = comment.get("id", "")

    if not issue_key or not raw_text:
        return

    classification = await ai.classify_message(raw_text)

    issue = db.query(Issue).filter(Issue.jira_key == issue_key).first()
    stage = issue.status.value if issue else "unknown"

    msg = Message(
        issue_key=issue_key,
        source=MessageSource.jira_comment,
        raw_text=raw_text,
        speaker_name=author,
        source_id=comment_id,
        msg_type=classification.get("type", "unclassified"),
        classification_confidence=classification.get("confidence"),
        timestamp=datetime.now(timezone.utc),
    )
    db.add(msg)

    await conversation_store.append_message(issue_key, stage, author, raw_text)
    db.commit()

