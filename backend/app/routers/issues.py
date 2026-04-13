"""
Issues CRUD + AI 分析确认节点
GET    /issues                  → issue 列表（支持 status 筛选）
GET    /issues/{key}            → issue 详情（如未分析则立即触发 AI）
POST   /issues/sync             → 从 JIRA 全量同步（admin）
POST   /issues/{key}/confirm-analysis  → 人类确认 AI 分析 → 写回 JIRA
POST   /issues/{key}/confirm-verify    → 验证结论确认 → 写回 JIRA
GET    /issues/{key}/conversation      → 读取对话归档
"""
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from pydantic import BaseModel

from app.database import get_db, SessionLocal
from app.models.issue import Issue, IssueStatus, IssueModule
from app.models.message import Message, MessageSource
from app.models.user import User
from app.services import jira as jira_svc, conversation_store, ai
from app.services.workflow import trigger_analysis, trigger_solving, trigger_verify
from app.schemas.issue import IssueOut, IssueListItem, ConfirmAnalysisIn, ConfirmVerifyIn
from app.routers.auth import get_current_user, require_admin

router = APIRouter(prefix="/issues", tags=["issues"])
logger = logging.getLogger(__name__)

_JIRA_STATUS_MAP = {
    "问题分析中": IssueStatus.analysis,
    "问题解决中": IssueStatus.solving,
    "效果验证":   IssueStatus.verify,
    "问题关闭":   IssueStatus.closed,
}

# 状态流转顺序（用于防止 JIRA 同步回退本地已推进的状态）
_STATUS_ORDER = {
    IssueStatus.analysis: 1,
    IssueStatus.solving:  2,
    IssueStatus.verify:   3,
    IssueStatus.closed:   4,
}


def _bg(coro):
    """在当前事件循环中异步启动一个后台任务（fire-and-forget）"""
    try:
        asyncio.get_event_loop().create_task(coro)
    except RuntimeError:
        pass


async def sync_issues_from_jira(db: Session) -> dict:
    """从 JIRA 拉取项目全部 issue，upsert 到本地 DB，并对未分析 issue 触发 AI。"""
    try:
        jira_issues = await jira_svc.get_all_acms_issues()
    except Exception as e:
        logger.error("JIRA sync failed: %s", e)
        raise

    created = updated = skipped = 0
    needs_analysis: list[Issue] = []   # 新建的分析中 issue → 触发 AI

    for ji in jira_issues:
        key    = ji.get("key", "")
        fields = ji.get("fields", {})
        if not key:
            continue

        title        = fields.get("summary", "")
        status_name  = fields.get("status", {}).get("name", "")
        status       = _JIRA_STATUS_MAP.get(status_name)
        description  = fields.get("description") or ""
        jira_created = _parse_jira_ts(fields.get("created"))
        jira_updated = _parse_jira_ts(fields.get("updated"))

        if status is None:
            skipped += 1
            continue

        existing = db.query(Issue).filter(Issue.jira_key == key).first()
        if existing:
            existing.title           = title
            existing.description     = description
            existing.jira_created_at = jira_created
            existing.jira_updated_at = jira_updated
            # 只在 JIRA 状态不低于本地状态时才更新（防止重启后同步回退已推进的阶段）
            jira_order  = _STATUS_ORDER.get(status, 0)
            local_order = _STATUS_ORDER.get(existing.status, 0)
            if jira_order >= local_order:
                existing.status = status
            updated += 1
            # 若 root_cause 已被清空且仍在分析阶段，加入重分析队列
            if status == IssueStatus.analysis and not existing.root_cause:
                needs_analysis.append(existing)
        else:
            new_issue = Issue(
                jira_key=key, title=title, status=status,
                description=description,
                jira_created_at=jira_created, jira_updated_at=jira_updated,
            )
            db.add(new_issue)
            db.flush()
            if status == IssueStatus.analysis:
                needs_analysis.append(new_issue)
            created += 1

    db.commit()

    # 对同步进来的未分析 issue 触发 AI（逐个异步，不阻塞 sync 返回）
    for issue in needs_analysis:
        _bg(_trigger_with_new_session(trigger_analysis, issue.id))

    logger.info("JIRA sync: created=%d updated=%d skipped=%d trigger_analysis=%d",
                created, updated, skipped, len(needs_analysis))
    return {"created": created, "updated": updated, "skipped": skipped, "total": len(jira_issues)}


async def _trigger_with_new_session(trigger_fn, issue_id: int):
    """用独立 DB session 执行 trigger，避免与主请求 session 冲突。"""
    db = SessionLocal()
    try:
        issue = db.query(Issue).filter(Issue.id == issue_id).first()
        if issue:
            await trigger_fn(issue, db)
    except Exception as e:
        logger.warning("Background trigger failed for issue_id=%d: %s", issue_id, e)
    finally:
        db.close()


def _parse_jira_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


async def startup_sync():
    """服务启动时静默同步一次，不阻塞启动流程。"""
    db = SessionLocal()
    try:
        result = await sync_issues_from_jira(db)
        logger.info("Startup JIRA sync done: %s", result)
    except Exception as e:
        logger.warning("Startup JIRA sync failed (non-fatal): %s", e)
    finally:
        db.close()


@router.get("", response_model=list[IssueListItem])
def list_issues(
    status: IssueStatus | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(Issue)
    if status:
        q = q.filter(Issue.status == status)
    return q.order_by(Issue.updated_at.desc()).all()


@router.post("/sync")
async def sync_from_jira(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Admin 手动触发从 JIRA 全量同步 issue。"""
    result = await sync_issues_from_jira(db)
    return {"ok": True, **result}


@router.get("/{key}", response_model=IssueOut)
async def get_issue(
    key: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    issue = db.query(Issue).filter(Issue.jira_key == key).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    # AI 主动推进：若该节点还没有 AI 内容，立即在后台触发
    if issue.status == IssueStatus.analysis and not issue.root_cause:
        _bg(_trigger_with_new_session(trigger_analysis, issue.id))
    elif issue.status == IssueStatus.solving:
        _bg(_trigger_with_new_session(trigger_solving, issue.id))
    elif issue.status == IssueStatus.verify:
        _bg(_trigger_with_new_session(trigger_verify, issue.id))

    return issue


@router.post("/{key}/confirm-analysis")
async def confirm_analysis(
    key: str,
    body: ConfirmAnalysisIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    节点 1 确认：人类审核 AI 分析结果。
    - 可选覆盖 root_cause / fix_solution
    - AI 分析结果写回 JIRA comment
    - JIRA issue 流转到「问题解决中」
    """
    issue = db.query(Issue).filter(Issue.jira_key == key).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.status != IssueStatus.analysis:
        raise HTTPException(status_code=400, detail="Issue is not in analysis stage")

    # 若人类修改了分析结果，以人类版本为准
    if body.root_cause is not None:
        issue.root_cause = body.root_cause
    if body.fix_solution is not None:
        issue.fix_solution = body.fix_solution
    issue.fix_confirmed_by = current_user.feishu_name

    # 写回 JIRA comment
    jira_comment = (
        f"【AI 根因分析 — 已由 {current_user.feishu_name} 确认】\n\n"
        f"**根因**：{issue.root_cause}\n\n"
        f"**修复方案**：{issue.fix_solution}\n\n"
        f"**代码位置**：{issue.fix_code_location or '待确认'}\n\n"
        f"**影响范围**：{issue.impact_scope or '待确认'}"
    )

    # 构建 JIRA transition 必填字段
    jira_fields: dict = {}
    pending = issue.jira_fields_pending or {}
    if pending.get("customfield_10912"):
        jira_fields["customfield_10912"] = pending["customfield_10912"]
    if pending.get("customfield_10907"):
        jira_fields["customfield_10907"] = pending["customfield_10907"]
    if pending.get("customfield_12507"):
        jira_fields["customfield_12507"] = pending["customfield_12507"]
    if pending.get("customfield_13103"):
        jira_fields["customfield_13103"] = {"value": pending["customfield_13103"]}
    # Bug责任人：尝试按姓名查 accountId；AI 未给出时兜底用确认人
    responsible_name = pending.get("customfield_17801_suggest", "")
    if not responsible_name or responsible_name in ("待确认", ""):
        responsible_name = current_user.feishu_name  # 兜底：由确认人担任责任人
    if responsible_name:
        account_id = await jira_svc.search_user(responsible_name)
        if account_id:
            jira_fields["customfield_17801"] = [{"name": account_id}]

    try:
        await jira_svc.add_comment(key, jira_comment)
        ok = await jira_svc.transition_issue(key, "问题解决中", fields=jira_fields or None)
        if not ok:
            logger.warning("JIRA transition failed for %s (status!=2xx)", key)
    except Exception as e:
        logger.warning("JIRA write-back failed for %s: %s", key, e)

    # 归档确认动作
    confirmation_text = (
        f"[确认了根因分析]\n\n"
        f"根因：{issue.root_cause}\n"
        f"修复方案：{issue.fix_solution}"
    )
    if body.comment:
        confirmation_text += f"\n\n备注：{body.comment}"
    await conversation_store.append_message(
        key, "问题分析中", current_user.feishu_name, confirmation_text
    )

    issue.status = IssueStatus.solving
    db.commit()

    # AI 主动推进：立即在后台为研发生成修复简报
    _bg(_trigger_with_new_session(trigger_solving, issue.id))

    return {"ok": True, "next_status": "问题解决中"}


@router.post("/{key}/confirm-verify")
async def confirm_verify(
    key: str,
    body: ConfirmVerifyIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    节点 3 确认：验证人员给出验证结论。
    - 写回 JIRA comment + 流转到「问题关闭」
    - AI 生成关闭报告（异步写入）
    """
    issue = db.query(Issue).filter(Issue.jira_key == key).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.status != IssueStatus.verify:
        raise HTTPException(status_code=400, detail="Issue is not in verify stage")

    issue.verify_result = body.result
    issue.verified_by = current_user.feishu_name

    # 归档验证结论
    verify_text = f"[验证结论] {body.result}"
    if body.comment:
        verify_text += f"\n\n{body.comment}"
    await conversation_store.append_message(
        key, "效果验证", current_user.feishu_name, verify_text
    )

    # 判断验证结果：通过 → 关闭；不通过 → 回退分析
    failed = any(kw in body.result for kw in ("不通过", "复现", "失败"))

    if failed:
        # 验证不通过：回退到问题分析中，重新触发 AI 分析
        rollback_comment = f"【验证不通过 — 回退重新分析】\n验证人：{current_user.feishu_name}\n结论：{body.result}"
        try:
            await jira_svc.add_comment(key, rollback_comment)
            await jira_svc.reopen_issue(key, "效果验证")
        except Exception as e:
            logger.warning("JIRA reopen failed for %s: %s", key, e)

        issue.status = IssueStatus.analysis
        db.commit()
        _bg(_trigger_with_new_session(trigger_analysis, issue.id))
        return {"ok": True, "next_status": "问题分析中", "action": "rollback"}

    # 验证通过：生成关闭报告并流转关闭
    issue_data = {
        "jira_key": issue.jira_key,
        "title": issue.title,
        "root_cause": issue.root_cause,
        "fix_solution": issue.fix_solution,
        "verify_result": issue.verify_result,
        "rejected_solutions": issue.rejected_solutions,
    }
    try:
        report = await ai.generate_close_report(issue_data)
        await jira_svc.add_comment(key, report)
        await jira_svc.transition_issue(key, "问题关闭")
    except Exception as e:
        logger.warning("JIRA close failed for %s: %s", key, e)
        report = "（报告生成失败）"

    await conversation_store.append_message(key, "效果验证", "boringbot", report)

    issue.status = IssueStatus.closed
    db.commit()

    return {"ok": True, "report": report}


@router.post("/{key}/rollback")
async def rollback_issue(
    key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    手动回退：「问题解决中」→「问题分析中」（根因有误，需重新分析）
    """
    issue = db.query(Issue).filter(Issue.jira_key == key).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.status != IssueStatus.solving:
        raise HTTPException(status_code=400, detail="只能从「问题解决中」手动回退")

    from_status = issue.status.value
    rollback_comment = f"【手动回退至问题分析中】\n操作人：{current_user.feishu_name}"
    try:
        await jira_svc.add_comment(key, rollback_comment)
        await jira_svc.reopen_issue(key, from_status)
    except Exception as e:
        logger.warning("JIRA rollback failed for %s: %s", key, e)

    await conversation_store.append_message(
        key, "问题解决中", current_user.feishu_name,
        f"[回退至问题分析中] 原因：根因分析需修订"
    )

    issue.status = IssueStatus.analysis
    db.commit()
    _bg(_trigger_with_new_session(trigger_analysis, issue.id))

    return {"ok": True, "next_status": "问题分析中"}


@router.get("/{key}/conversation")
async def get_conversation(
    key: str,
    stage: str | None = Query(None),
    _: User = Depends(get_current_user),
):
    """读取 issue 对话归档 markdown"""
    text = await conversation_store.read_conversation(key, stage)
    return {"issue_key": key, "stage": stage, "raw_markdown": text}


@router.get("/{key}/jira-transitions")
async def jira_transitions(
    key: str,
    _: User = Depends(get_current_user),
):
    """调试用：查看该 issue 当前可用的 JIRA transition id（用于填写 jira.py 里的 JIRA_STATUS_TRANSITION）"""
    transitions = await jira_svc.get_transitions(key)
    return {"issue_key": key, "transitions": [{"id": t["id"], "name": t["name"]} for t in transitions]}


class RecordChangeIn(BaseModel):
    diff:   str
    status: str = ""


@router.post("/{key}/record-change")
async def record_change(
    key: str,
    body: RecordChangeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    VS Code 插件调用：将 git diff 作为消息归档到对话记录。
    不改变 issue 状态，仅记录变更快照。
    """
    issue = db.query(Issue).filter(Issue.jira_key == key).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    now = datetime.now(timezone.utc)

    # 组装消息正文
    parts = [f"**代码变更记录**（{current_user.feishu_name} via VS Code）"]
    if body.status:
        parts.append(f"\n文件变更：\n```\n{body.status}\n```")
    if body.diff:
        parts.append(f"\n```diff\n{body.diff[:4000]}\n```")
    raw_text = "\n".join(parts)

    msg = Message(
        issue_key=key,
        source=MessageSource.user_chat,
        raw_text=raw_text,
        speaker_name=current_user.feishu_name,
        speaker_feishu_uid=current_user.feishu_uid,
        msg_type="action",
        timestamp=now,
    )
    db.add(msg)

    # 同时写入对话文件（会出现在 Web 聊天记录中）
    stage = issue.status.value
    await conversation_store.append_message(key, stage, current_user.feishu_name, raw_text, now)

    db.commit()
    return {"ok": True}
