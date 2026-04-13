"""
Chat API — 前端发消息 → AI 处理 → 可选写回 JIRA
POST /chat/{issue_key}          → 发送一条消息，获取 AI 回复
"""
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.database import get_db
from app.models.issue import Issue, IssueStatus
from app.models.message import Message, MessageSource
from app.models.user import User
from app.services import ai, conversation_store
from app.schemas.chat import ChatMessageIn, ChatMessageOut
from app.routers.auth import get_current_user

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)

# Per-issue lock：同一 issue 同时只处理一条消息，防止并发时历史上下文混乱
_issue_locks: dict[str, asyncio.Lock] = {}

# 驾驶舱全局对话历史（按用户 uid 隔离）
_global_history: dict[str, list[dict]] = {}

# 每个阶段的 AI 上下文提示
STAGE_CONTEXT = {
    IssueStatus.analysis: "当前阶段：问题分析中。你的目标是帮助研发人员确认根因和修复方案。",
    IssueStatus.solving:  "当前阶段：问题解决中。代码已修改，帮助研发人员记录变更并推进验证。",
    IssueStatus.verify:   "当前阶段：效果验证中。协助验证人员整理测试结论。",
    IssueStatus.closed:   "当前阶段：问题已关闭。",
}


@router.post("/global", response_model=ChatMessageOut)
async def send_global_message(
    body: ChatMessageIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    驾驶舱全局对话：管理视角问答，支持 Mermaid 图表
    """
    # 汇总项目数据作为 AI 上下文
    issues = db.query(Issue).all()
    counts: dict[str, int] = {}
    for iss in issues:
        counts[iss.status.value] = counts.get(iss.status.value, 0) + 1

    # 列出最近 5 个未关闭 issue 标题，给 AI 更多细节
    open_issues = [i for i in issues if i.status.value != "问题关闭"]
    open_issues_sorted = sorted(open_issues, key=lambda x: x.updated_at, reverse=True)[:5]
    open_lines = "\n".join(
        f"- {i.jira_key}（{i.status.value}）：{i.title}"
        for i in open_issues_sorted
    )

    stats_context = (
        f"总 issue 数：{len(issues)}\n"
        f"问题分析中：{counts.get('问题分析中', 0)}\n"
        f"问题解决中：{counts.get('问题解决中', 0)}\n"
        f"效果验证：{counts.get('效果验证', 0)}\n"
        f"已关闭：{counts.get('问题关闭', 0)}\n\n"
        f"最近未关闭 issue：\n{open_lines or '（无）'}"
    )

    history = _global_history.get(current_user.feishu_uid, [])
    ai_text = await ai.dashboard_chat(stats_context, history, body.text)

    history.append({"role": "user",      "content": body.text})
    history.append({"role": "assistant", "content": ai_text})
    _global_history[current_user.feishu_uid] = history

    now = datetime.now(timezone.utc)
    return ChatMessageOut(speaker="boringbot", text=ai_text, timestamp=now)


@router.post("/{issue_key}", response_model=ChatMessageOut)
async def send_message(
    issue_key: str,
    body: ChatMessageIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    用户发送消息：
    1. 对消息分类并归档
    2. 读取当前对话历史作为上下文
    3. 调用 AI 生成回复
    4. 归档 AI 回复
    """
    issue = db.query(Issue).filter(Issue.jira_key == issue_key).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    # 同一 issue 串行处理，防止并发时历史上下文混乱
    lock = _issue_locks.setdefault(issue_key, asyncio.Lock())
    async with lock:
        return await _process_message(issue, issue_key, body, db, current_user)


async def _process_message(issue, issue_key, body, db, current_user):
    stage = issue.status.value
    now = datetime.now(timezone.utc)

    # 1. 分类并存储用户消息
    classification = await ai.classify_message(body.text)

    msg = Message(
        issue_key=issue_key,
        source=MessageSource.user_chat,
        raw_text=body.text,
        speaker_name=current_user.feishu_name,
        speaker_feishu_uid=current_user.feishu_uid,
        msg_type=classification.get("type", "unclassified"),
        classification_confidence=classification.get("confidence"),
        timestamp=now,
    )
    db.add(msg)
    db.commit()

    await conversation_store.append_message(
        issue_key, stage, current_user.feishu_name, body.text, now
    )

    # 2. 读取对话历史作为上下文
    history_md = await conversation_store.read_conversation(issue_key, stage)
    prior_md   = await conversation_store.read_prior_context(issue_key, stage)

    # 3. 调用 AI（含历史上下文）
    stage_hint = STAGE_CONTEXT.get(issue.status, "")
    issue_context = (
        f"Issue: {issue_key}\n"
        f"标题: {issue.title}\n"
        f"根因: {issue.root_cause or '待分析'}\n"
        f"修复方案: {issue.fix_solution or '待制定'}"
    )

    history_messages = []
    if prior_md:
        history_messages += [
            {"role": "user",      "content": f"[前置阶段记录]\n{prior_md[-2000:]}"},
            {"role": "assistant", "content": "好的，我已了解前置阶段历史。"},
        ]
    if history_md:
        history_messages += [
            {"role": "user",      "content": f"[当前阶段对话历史]\n{history_md[-2000:]}"},
            {"role": "assistant", "content": "好的，我已了解当前阶段对话历史。"},
        ]

    ai_text = await ai.chat_reply(issue_context, stage_hint, history_messages, body.text)

    # In analysis stage: extract any explicit field updates the user requested and persist
    if issue.status == IssueStatus.analysis:
        try:
            upd = await ai.extract_analysis_update(body.text, ai_text)
            changed = False
            if upd.get("root_cause"):
                issue.root_cause = upd["root_cause"]
                changed = True
            if upd.get("fix_solution"):
                issue.fix_solution = upd["fix_solution"]
                changed = True
            if changed:
                db.commit()
        except Exception as e:
            logger.warning("extract_analysis_update failed: %s", e)

    # 4. 归档 AI 回复
    ai_now = datetime.now(timezone.utc)
    await conversation_store.append_message(issue_key, stage, "boringbot", ai_text, ai_now)

    ai_msg = Message(
        issue_key=issue_key,
        source=MessageSource.system,
        raw_text=ai_text,
        speaker_name="boringbot",
        msg_type="decision",
        timestamp=ai_now,
    )
    db.add(ai_msg)
    db.commit()

    return ChatMessageOut(speaker="boringbot", text=ai_text, timestamp=ai_now)
