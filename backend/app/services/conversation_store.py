"""
对话归档服务 — 原文明文写入 Markdown，append-only。
路径规则: {conversations_dir}/{issue_key}/{date}_{stage}.md
"""
import aiofiles
import os
from datetime import datetime, timezone
from pathlib import Path
from app.config import get_settings

settings = get_settings()

STAGE_SLUG = {
    "问题分析中": "analysis",
    "问题解决中": "solving",
    "效果验证":   "verify",
    "问题关闭":   "closed",
}

STAGE_ORDER = ["问题分析中", "问题解决中", "效果验证", "问题关闭"]


def _conv_path(issue_key: str, stage: str, date: str | None = None) -> Path:
    slug = STAGE_SLUG.get(stage, stage.lower().replace(" ", "-"))
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = Path(settings.conversations_dir)
    return base / issue_key / f"{date}_{slug}.md"


async def ensure_file(
    issue_key: str,
    stage: str,
    participants: list[str],
    gitlab_branch: str | None = None,
    similar_issues: list[str] | None = None,
) -> Path:
    """确保归档文件存在，不存在则创建并写 frontmatter"""
    path = _conv_path(issue_key, stage)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        lines = ["---"]
        lines.append(f"issue: {issue_key}")
        lines.append(f"stage: {stage}")
        lines.append(f"started_at: {datetime.now(timezone.utc).isoformat()}")
        lines.append(f"participants: [{', '.join(participants)}]")
        lines.append("source: chat")
        if gitlab_branch:
            lines.append(f"gitlab_branch: {gitlab_branch}")
        if similar_issues:
            lines.append(f"similar_issues: [{', '.join(similar_issues)}]")
        lines.append("---\n")
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write("\n".join(lines))

    return path


async def append_message(
    issue_key: str,
    stage: str,
    speaker: str,
    text: str,
    timestamp: datetime | None = None,
) -> str:
    """追加一条消息到对应归档文件，返回文件路径"""
    path = _conv_path(issue_key, stage)
    if not path.exists():
        await ensure_file(issue_key, stage, participants=[speaker])

    ts = (timestamp or datetime.now(timezone.utc)).strftime("%H:%M")
    entry = f"\n**{speaker}** `{ts}`\n\n{text}\n\n---\n"

    async with aiofiles.open(path, "a", encoding="utf-8") as f:
        await f.write(entry)

    return str(path)


async def read_conversation(issue_key: str, stage: str | None = None) -> str:
    """读取 issue 的对话归档（stage 为 None 时读全部）"""
    base = Path(settings.conversations_dir) / issue_key
    if not base.exists():
        return ""

    files = sorted(base.glob("*.md"))
    if stage:
        slug = STAGE_SLUG.get(stage, stage)
        files = [f for f in files if slug in f.name]

    parts = []
    for f in files:
        async with aiofiles.open(f, encoding="utf-8") as fp:
            parts.append(await fp.read())

    return "\n\n".join(parts)


async def read_prior_context(issue_key: str, current_stage: str) -> str:
    """读取 current_stage 之前所有阶段的对话，按逻辑顺序拼接（供 AI 了解上下文）。"""
    try:
        idx = STAGE_ORDER.index(current_stage)
    except ValueError:
        return ""
    parts = []
    for stage in STAGE_ORDER[:idx]:
        content = await read_conversation(issue_key, stage)
        if content:
            parts.append(f"=== {stage} ===\n{content}")
    return "\n\n".join(parts)
