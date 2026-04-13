"""
AI 服务 — 对接公司 LLM Gateway（OpenAI-compatible /v1/chat/completions）
"""
import json
import httpx
from app.config import get_settings

settings = get_settings()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.llm_base_url,
        headers={
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        },
        timeout=60,
    )


def _strip_json(text: str) -> str:
    """提取 LLM 回复中的 JSON，兼容以下情况：
    1. 纯 JSON
    2. ```json ... ``` 包裹
    3. 说明文字 + ```json ... ```
    """
    text = text.strip()
    # 找 markdown 代码块
    start = text.find("```")
    if start != -1:
        # 跳过第一行（```json 或 ```）
        inner_start = text.find("\n", start) + 1
        end = text.rfind("```")
        if end > inner_start:
            return text[inner_start:end].strip()
    # 没有代码块，尝试直接找 { 开头的 JSON
    brace = text.find("{")
    if brace != -1:
        return text[brace:].strip()
    return text


async def _chat(system: str, user: str, max_tokens: int = 512) -> str:
    """最小化 chat 调用，返回 assistant 回复文本"""
    payload = {
        "model": settings.ai_model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    async with _client() as c:
        resp = await c.post("/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def _chat_with_history(system: str, messages: list[dict], max_tokens: int = 512) -> str:
    """带历史消息的 chat 调用"""
    payload = {
        "model": settings.ai_model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    async with _client() as c:
        resp = await c.post("/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ── 消息分类 ─────────────────────────────────────────────────────

CLASSIFY_SYSTEM = """你是 AutoCharge Cowork 的 AI 书记员。
将给定消息分类为以下类型之一，只返回 JSON，不要其他内容。

类型：
- hypothesis  假设或推测（"可能是..."、"猜测是..."）
- decision    已确定的决策或结论（"决定..."、"确认..."）
- data        测量值、日志、测试结果
- action      已执行的操作（"已推代码"、"已调整参数"）
- question    未回答的问题
- noise       无信息量（"好的"、"收到"）

返回格式：{"type": "<类型>", "confidence": <0.0-1.0>}"""


async def classify_message(text: str) -> dict:
    """对单条消息进行分类，返回 {type, confidence}"""
    raw = await _chat(CLASSIFY_SYSTEM, text, max_tokens=64)
    try:
        return json.loads(_strip_json(raw))
    except Exception:
        return {"type": "unclassified", "confidence": 0.0}


# ── 根因分析 + 修复方案 ──────────────────────────────────────────

ANALYSIS_SYSTEM = """你是 AutoCharge ACMS 项目的 AI 研发助手。

你的任务：基于 issue 信息和历史相似 issue，给出：
1. 根因分析（简洁，1-2句）
2. 推荐修复方案（具体，基于实际提供的代码或历史信息）
3. 排除方案列表（每条附理由）
4. 影响范围（哪些车型/模块）
5. JIRA 字段建议（供写回 JIRA 时使用）

输出 JSON 格式，不要 Markdown 包裹：
{
  "root_cause": "...",
  "fix_solution": "...",
  "fix_code_location": null,
  "rejected": [{"plan": "...", "reason": "..."}],
  "impact_scope": "...",
  "similar_issue_refs": ["ACMS-xx"],
  "confidence": 0.0,
  "jira_fields": {
    "customfield_10912": "原因分析简述（同 root_cause，200字以内）",
    "customfield_10907": "YYYY-MM-DD（从今天起约2-3周的预计修复日期）",
    "customfield_12507": "预计修复的软件版本号（从 issue 描述推断，不确定则填 待确认）",
    "customfield_13103": "从以下选一个: 功能/性能-流量监控/性能-CPU、内存/性能-响应时间/稳定性/兼容性/功耗/硬件/其它",
    "customfield_17801_suggest": "Bug责任人姓名（从 issue 描述或历史信息推断，不确定则填 待确认）"
  }
}

【fix_code_location 填写规则 — 严格执行】
- 若用户消息中包含「相关代码」部分（标记为 // File: xxx），fix_code_location 才可填写
- 填写时路径必须与「// File: xxx」中的路径完全一致，不得修改、缩短或猜测
- 格式为 "文件相对路径:行号"，多个位置用分号分隔，例如 "machine_arm/arm.cpp:387;machine_arm/arm.h:80"
- 若没有提供真实代码片段，fix_code_location 必须为 null，禁止凭空编造路径"""


async def analyze_issue(
    issue_key: str,
    title: str,
    description: str,
    similar_issues: list[dict],
    gitlab_code_snippet: str | None = None,
) -> dict:
    """分析 issue 根因，返回结构化分析结果"""
    parts = [f"Issue: {issue_key}\n标题: {title}\n描述: {description or '（空）'}"]

    if similar_issues:
        refs = "\n".join(
            f"- {s['key']}: {s['title']} | 根因: {s.get('root_cause','未知')} | 方案: {s.get('fix_solution','未知')}"
            for s in similar_issues
        )
        parts.append(f"\n历史相似 issue:\n{refs}")

    if gitlab_code_snippet:
        parts.append(f"\n相关代码:\n```\n{gitlab_code_snippet}\n```")

    raw = await _chat(ANALYSIS_SYSTEM, "\n".join(parts), max_tokens=1024)
    try:
        return json.loads(_strip_json(raw))
    except Exception:
        return {"root_cause": raw, "confidence": 0.0}


# ── 问题关闭报告 ─────────────────────────────────────────────────

SOLVING_SYSTEM = """你是 AutoCharge ACMS 项目的 AI 研发助手 boringbot。
问题已完成根因分析，研发同学即将开始修复工作。

请根据根因和修复方案，生成一份简洁的「修复任务简报」，帮助研发同学快速上手，主动推进：
1. 核心修复点（1-3条，具体到文件/函数/参数）
2. 需要特别注意的风险点
3. 建议的验证方式（本地可跑的测试或场景）

用中文，Markdown 格式，200字以内，不要废话。
【严禁】不得提及任何状态流转操作，不得要求用户回复特定关键词（如「提测」「完成」等），状态流转由界面按钮控制，与你无关。"""


async def generate_solving_brief(issue_data: dict, prior_context: str = "") -> str:
    """进入「问题解决中」时，AI 主动生成修复任务简报"""
    prompt = (
        f"Issue: {issue_data.get('jira_key')}\n"
        f"标题: {issue_data.get('title')}\n"
        f"根因: {issue_data.get('root_cause', '待确认')}\n"
        f"修复方案: {issue_data.get('fix_solution', '待确认')}\n"
        f"代码位置: {issue_data.get('fix_code_location', '待确认')}\n"
        f"影响范围: {issue_data.get('impact_scope', '待确认')}"
    )
    if prior_context:
        prompt += f"\n\n--- 前置阶段对话记录（摘要参考）---\n{prior_context[-2000:]}"
    return await _chat(SOLVING_SYSTEM, prompt, max_tokens=512)


VERIFY_SYSTEM = """你是 AutoCharge ACMS 项目的 AI 研发助手 boringbot。
问题已完成修复，即将进入效果验证阶段。

请根据根因和修复方案，主动生成一份「验证检查清单」，帮助验证人员高效完成验证：
1. 核心验证场景（必测，直接对应根因）
2. 回归场景（防止引入新问题）
3. 通过标准（明确的判定条件）

用中文，Markdown 格式，200字以内，清单格式，不要废话。
【严禁】不得提及任何状态流转操作，不得要求用户回复特定关键词，状态流转由界面按钮控制，与你无关。"""


async def generate_verify_checklist(issue_data: dict, prior_context: str = "") -> str:
    """进入「效果验证」时，AI 主动生成验证检查清单"""
    prompt = (
        f"Issue: {issue_data.get('jira_key')}\n"
        f"标题: {issue_data.get('title')}\n"
        f"根因: {issue_data.get('root_cause', '待确认')}\n"
        f"修复方案: {issue_data.get('fix_solution', '待确认')}\n"
        f"代码位置: {issue_data.get('fix_code_location', '待确认')}\n"
        f"影响范围: {issue_data.get('impact_scope', '待确认')}"
    )
    if prior_context:
        prompt += f"\n\n--- 前置阶段对话记录（摘要参考）---\n{prior_context[-2000:]}"
    return await _chat(VERIFY_SYSTEM, prompt, max_tokens=512)




REPORT_SYSTEM = """你是 AutoCharge ACMS 项目的 AI 书记员。
根据提供的 issue 全流程数据，生成简洁的问题关闭报告。
用中文，Markdown 格式，控制在 300 字以内。

必须包含：问题描述、根因、修复方案、被否定方案（如有）、验证结论、知识复用价值。"""


async def generate_close_report(issue_data: dict) -> str:
    """生成 issue 关闭报告（Markdown 格式）"""
    return await _chat(REPORT_SYSTEM, str(issue_data), max_tokens=512)


# ── 对话回复 ─────────────────────────────────────────────────────

DELTA_SYSTEM = """你是 AutoCharge ACMS 项目的 AI 执行助手 boringbot。
用简洁的中文回复，直接推进当前工作节点。
回复时不要重复已知信息，只给出最关键的下一步指引或结论。
【严禁】不得提及状态流转操作，不得要求用户回复特定关键词来触发任何操作，所有状态流转由界面按钮控制。"""


# ── 驾驶舱全局对话 ───────────────────────────────────────────────

DASHBOARD_SYSTEM = """你是 AutoCharge ACMS 项目的 AI 管理助手 boringbot，正在与项目负责人进行驾驶舱对话。
职责：
- 基于当前项目数据，回答项目进展、瓶颈、优先级相关问题
- 当用户要求趋势图、分布图、饼图、柱状图等可视化时，输出 ECharts option JSON
- 提供宏观视角的管理建议，数据驱动

绘图规则：
- 图表必须用 ```echarts 代码块包裹，内容为合法 ECharts option JSON
- 饼图示例：
```echarts
{"title":{"text":"问题状态分布"},"tooltip":{"trigger":"item"},"legend":{"bottom":"5%"},"series":[{"type":"pie","radius":["40%","65%"],"data":[{"value":3,"name":"分析中"},{"value":2,"name":"解决中"}]}]}
```
- 柱状图示例：
```echarts
{"title":{"text":"各模块 issue 数"},"tooltip":{},"xAxis":{"type":"category","data":["感知","控制","规划"]},"yAxis":{"type":"value"},"series":[{"type":"bar","data":[5,3,4]}]}
```
- 确保 JSON 格式正确，不要在 JSON 中加注释

回复注意：
- 不要给出研发级技术操作指引，不要提「当前待办」
- 简洁、管理视角"""


async def dashboard_chat(
    stats_context: str,
    history_messages: list[dict],
    user_text: str,
) -> str:
    """驾驶舱全局管理对话"""
    system = DASHBOARD_SYSTEM + f"\n\n当前项目数据：\n{stats_context}"
    messages = history_messages + [{"role": "user", "content": user_text}]
    return await _chat_with_history(system, messages, max_tokens=800)


async def chat_reply(
    issue_context: str,
    stage_hint: str,
    history_messages: list[dict],
    user_text: str,
) -> str:
    """生成 AI 对话回复"""
    system = DELTA_SYSTEM + f"\n\n{issue_context}\n{stage_hint}"
    messages = history_messages + [{"role": "user", "content": user_text}]
    return await _chat_with_history(system, messages, max_tokens=512)


# ── 分析字段更新提取 ──────────────────────────────────────────────

EXTRACT_UPDATE_SYSTEM = """判断用户是否在对话中明确要求修改根因（root_cause）或修复方案（fix_solution）。
只在用户明确提出修改时提取新值，否则对应字段返回 null。
只返回 JSON，不要其他内容：{"root_cause": "新根因文本或null", "fix_solution": "新方案文本或null"}"""


async def extract_analysis_update(user_text: str, ai_reply: str) -> dict:
    """从对话中提取用户要求的分析字段更新，无更新则返回空值"""
    prompt = f"用户说：{user_text[:500]}\nAI回复：{ai_reply[:800]}"
    raw = await _chat(EXTRACT_UPDATE_SYSTEM, prompt, max_tokens=256)
    try:
        result = json.loads(_strip_json(raw))
        # Filter out "null" string values
        return {k: v for k, v in result.items() if v and v != "null"}
    except Exception:
        return {}


# ── 问题提报字段填充 ──────────────────────────────────────────────

REPORT_FILL_SYSTEM = """你是 AutoCharge ACMS 项目的 AI 问题提报助手 boringbot。
测试人员正在描述一个新发现的问题，你的任务是：
1. 倾听并整理问题描述
2. 从对话中提取结构化信息，填写 JIRA 提报字段
3. 主动追问缺失的关键信息（问题时间、车型/车位、具体现象、复现步骤）

每次回复结束后，必须在末尾附上当前草稿字段的 JSON（即使部分字段为 null）：
```json
{
  "title": "【模块】问题简述，不超过50字",
  "description": "详细问题描述，包含现象、环境、影响",
  "component": "从以下选一个: 机械臂控制 / 感知 / 硬件设计 / 供应商接口 / 未分类",
  "severity": "从以下选一个: P1-紧急 / P2-高 / P3-中 / P4-低",
  "steps_to_reproduce": "复现步骤（如有）",
  "time_range": "故障发生时间段，格式 YYYY-MM-DD HH:MM ~ HH:MM，不确定则填 null"
}
```

用中文回复，语气简洁专业，不超过150字（不含 JSON）。
【严禁】不得提及任何状态流转操作。"""


async def fill_report_fields(
    conversation_history: list[dict],
    user_text: str,
    log_summary: str = "",
) -> tuple[str, dict]:
    """
    根据对话历史 + 当前用户消息，更新提报草稿字段。
    返回 (ai_reply_text, draft_fields_dict)
    """
    system = REPORT_FILL_SYSTEM
    messages = conversation_history + [{"role": "user", "content": user_text}]
    if log_summary:
        messages = [
            {"role": "user", "content": f"[系统日志摘要]\n{log_summary}"},
            {"role": "assistant", "content": "好的，我已查看日志摘要。"},
        ] + messages

    raw = await _chat_with_history(system, messages, max_tokens=768)

    # 从回复中提取 JSON 草稿
    draft: dict = {}
    try:
        # 找到末尾 JSON 块
        json_start = raw.rfind("```json")
        if json_start != -1:
            inner = raw[json_start:]
            draft = json.loads(_strip_json(inner))
            # 移除回复中的 JSON 部分，只保留对话文本
            reply_text = raw[:json_start].strip()
        else:
            brace = raw.rfind("{")
            if brace != -1:
                try:
                    draft = json.loads(raw[brace:])
                    reply_text = raw[:brace].strip()
                except Exception:
                    reply_text = raw
            else:
                reply_text = raw
    except Exception:
        reply_text = raw

    # 过滤 null 字符串
    draft = {k: v for k, v in draft.items() if v and v != "null"}
    return reply_text, draft
