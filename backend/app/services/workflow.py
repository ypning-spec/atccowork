"""
AI 工作流触发器 — 各节点主动推进逻辑

每个节点的设计原则：
  AI 不等人问，主动根据 JIRA 描述/历史 issue 生成分析/简报/清单，
  写入对话归档，供对应角色直接审核确认。

触发点：
  trigger_analysis  → 进入「问题分析中」时（webhook 或 sync）
  trigger_solving   → 确认分析后进入「问题解决中」时（confirm_analysis）
  trigger_verify    → 进入「效果验证」时（JIRA webhook 状态变更）
"""
import logging
from sqlalchemy.orm import Session

from app.models.issue import Issue, IssueStatus
from app.services import ai, conversation_store, code_search
from app.config import get_settings

logger = logging.getLogger(__name__)

# 防止同一 issue 同一阶段被并发重复触发
_analysis_running: set[int] = set()
_solving_running:  set[int] = set()
_verify_running:   set[int] = set()


async def trigger_analysis(issue: Issue, db: Session) -> None:
    """节点1：AI 拉取相似 issue，分析根因，写入归档，等待人类确认。
    幂等：若 root_cause 已存在或正在运行，跳过。
    """
    if issue.root_cause:
        return
    if issue.id in _analysis_running:
        return  # 并发保护：已有相同 issue 的分析正在进行
    _analysis_running.add(issue.id)
    try:
        # 找相似 issue（已关闭且有根因）
        similar = (
            db.query(Issue)
            .filter(Issue.status == IssueStatus.closed, Issue.root_cause.isnot(None))
            .order_by(Issue.updated_at.desc())
            .limit(3)
            .all()
        )
        similar_dicts = [
            {
                "key": s.jira_key,
                "title": s.title,
                "root_cause": s.root_cause,
                "fix_solution": s.fix_solution,
            }
            for s in similar
        ]

        # 扫描本地代码库，获取真实代码片段（避免 AI 幻觉路径）
        settings = get_settings()
        code_snippet: str | None = None
        if settings.codebase_path:
            keywords = code_search.extract_keywords(
                issue.title, issue.description or ""
            )
            search_results = code_search.search_codebase(
                settings.codebase_path, keywords, max_files=6
            )
            code_snippet = code_search.format_for_prompt(search_results) or None
            if code_snippet:
                logger.info(
                    "code_search found %d files for %s: %s",
                    len(search_results), issue.jira_key,
                    [r["file"] for r in search_results],
                )
            else:
                logger.info("code_search: no relevant files found for %s", issue.jira_key)
        else:
            logger.info("codebase_path not configured, skipping code scan for %s", issue.jira_key)

        # AI 分析（传入真实代码片段，无则为 None）
        analysis = await ai.analyze_issue(
            issue_key=issue.jira_key,
            title=issue.title,
            description=issue.description or "",
            similar_issues=similar_dicts,
            gitlab_code_snippet=code_snippet,
        )

        # 写入归档文件头
        await conversation_store.ensure_file(
            issue.jira_key,
            "问题分析中",
            participants=["boringbot"],
            similar_issues=[s["key"] for s in similar_dicts],
        )

        # 组织 AI 主动推送的消息（不重复卡片内容，只做简短说明）
        jf = analysis.get("jira_fields", {})
        jira_hint = ""
        if jf:
            jira_hint = (
                "\n\n建议写入 JIRA 的字段：\n"
                f"- 原因分析：{jf.get('customfield_10912', '待补充')}\n"
                f"- 预计完成时间：{jf.get('customfield_10907', '待确认')}\n"
                f"- 预计修复版本：{jf.get('customfield_12507', '待确认')}\n"
                f"- 问题类别：{jf.get('customfield_13103', '待确认')}\n"
                f"- Bug 责任人：{jf.get('customfield_17801_suggest', '待确认')}"
            )
        ai_text = (
            f"根因初步推断已完成，详见上方分析卡。"
            f"{jira_hint}\n\n"
            f"如有异议请直接告知，无异议请点击下方「确认分析并推进」。"
        )

        await conversation_store.append_message(
            issue.jira_key, "问题分析中", "boringbot", ai_text
        )

        # 持久化分析结果
        issue.root_cause          = analysis.get("root_cause")
        issue.fix_solution        = analysis.get("fix_solution")
        issue.impact_scope        = analysis.get("impact_scope")
        issue.rejected_solutions  = analysis.get("rejected", [])
        issue.fix_code_location   = analysis.get("fix_code_location")
        issue.similar_issue_keys  = analysis.get("similar_issue_refs", [])
        issue.jira_fields_pending = analysis.get("jira_fields")
        db.commit()
        logger.info("trigger_analysis done for %s", issue.jira_key)
    finally:
        _analysis_running.discard(issue.id)


async def trigger_solving(issue: Issue, db: Session) -> None:
    """节点2：进入「问题解决中」后，AI 主动生成修复任务简报，直接发给研发。
    幂等：若 solving 阶段归档文件已有 boringbot 消息，或正在运行，跳过。
    """
    existing = await conversation_store.read_conversation(issue.jira_key, "问题解决中")
    if existing and "boringbot" in existing:
        return
    if issue.id in _solving_running:
        return
    _solving_running.add(issue.id)
    try:
        issue_data = {
            "jira_key":          issue.jira_key,
            "title":             issue.title,
            "root_cause":        issue.root_cause or "待确认",
            "fix_solution":      issue.fix_solution or "待确认",
            "fix_code_location": issue.fix_code_location or "待确认",
            "impact_scope":      issue.impact_scope or "待确认",
        }

        prior_ctx = await conversation_store.read_prior_context(issue.jira_key, "问题解决中")
        brief = await ai.generate_solving_brief(issue_data, prior_context=prior_ctx)

        await conversation_store.ensure_file(
            issue.jira_key, "问题解决中", participants=["boringbot"]
        )
        await conversation_store.append_message(
            issue.jira_key, "问题解决中", "boringbot", brief
        )
        logger.info("trigger_solving done for %s", issue.jira_key)
    finally:
        _solving_running.discard(issue.id)


async def trigger_verify(issue: Issue, db: Session) -> None:
    """节点3：进入「效果验证」后，AI 主动生成验证清单，直接发给验证人员。
    幂等：若 verify 阶段归档文件已有 boringbot 消息，或正在运行，跳过。
    """
    existing = await conversation_store.read_conversation(issue.jira_key, "效果验证")
    if existing and "boringbot" in existing:
        return
    if issue.id in _verify_running:
        return
    _verify_running.add(issue.id)
    try:
        issue_data = {
            "jira_key":          issue.jira_key,
            "title":             issue.title,
            "root_cause":        issue.root_cause or "待确认",
            "fix_solution":      issue.fix_solution or "待确认",
            "fix_code_location": issue.fix_code_location or "待确认",
            "impact_scope":      issue.impact_scope or "待确认",
        }

        prior_ctx = await conversation_store.read_prior_context(issue.jira_key, "效果验证")
        checklist = await ai.generate_verify_checklist(issue_data, prior_context=prior_ctx)

        await conversation_store.ensure_file(
            issue.jira_key, "效果验证", participants=["boringbot"]
        )
        await conversation_store.append_message(
            issue.jira_key, "效果验证", "boringbot", checklist
        )
        logger.info("trigger_verify done for %s", issue.jira_key)
    finally:
        _verify_running.discard(issue.id)

