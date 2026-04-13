"""JIRA REST API 封装（Server / Data Center — API v2 + PAT）"""
import httpx
from app.config import get_settings

settings = get_settings()

# 正向流转（happy path）
JIRA_STATUS_TRANSITION: dict[str, str | None] = {
    "问题分析中": None,    # 初始状态，无需 transition
    "问题解决中": "11",    # 分析完成 → 问题解决中
    "效果验证":   "211",   # 解决完成 → 效果验证
    "问题关闭":   "111",   # 验证通过 → 问题关闭
}

# 逆向流转（验证不通过 / 重新分析）
JIRA_REOPEN_TRANSITION: dict[str, str] = {
    "效果验证→问题分析中": "101",   # 验证不通过 → 问题分析中
    "问题解决中→问题分析中": "171", # 重新分析 → 问题分析中
    "问题解决中→问题解决中": "251", # 缺陷更新（自循环）
}


def _client() -> httpx.AsyncClient:
    """返回带认证头、自动跟随重定向的 HTTP 客户端"""
    return httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {settings.jira_api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        follow_redirects=True,
        timeout=15,
    )


def _v2(path: str) -> str:
    return f"{settings.jira_base_url}/rest/api/2{path}"


async def get_issue(issue_key: str) -> dict:
    """获取 JIRA issue 详情"""
    async with _client() as c:
        resp = await c.get(_v2(f"/issue/{issue_key}"))
        resp.raise_for_status()
        return resp.json()


async def add_comment(issue_key: str, body_text: str) -> dict:
    """在 JIRA issue 上添加评论（纯文本 / Wiki Markup）"""
    async with _client() as c:
        resp = await c.post(
            _v2(f"/issue/{issue_key}/comment"),
            json={"body": body_text},
        )
        resp.raise_for_status()
        return resp.json()


async def get_transitions(issue_key: str) -> list[dict]:
    """获取 issue 当前可用的 transitions（用于查看 id）"""
    async with _client() as c:
        resp = await c.get(_v2(f"/issue/{issue_key}/transitions"))
        resp.raise_for_status()
        return resp.json().get("transitions", [])


async def transition_issue(issue_key: str, target_status: str, fields: dict | None = None) -> bool:
    """将 issue 流转到目标状态，fields 为 transition screen 上的必填字段"""
    transition_id = JIRA_STATUS_TRANSITION.get(target_status)
    if not transition_id:
        return False
    body: dict = {"transition": {"id": transition_id}}
    if fields:
        body["fields"] = fields
    async with _client() as c:
        resp = await c.post(
            _v2(f"/issue/{issue_key}/transitions"),
            json=body,
        )
        return resp.status_code in (200, 204)


async def reopen_issue(issue_key: str, from_status: str) -> bool:
    """将 issue 回退到「问题分析中」（验证不通过 / 需重新分析）"""
    key = f"{from_status}→问题分析中"
    transition_id = JIRA_REOPEN_TRANSITION.get(key)
    if not transition_id:
        return False
    body: dict = {"transition": {"id": transition_id}}
    async with _client() as c:
        resp = await c.post(
            _v2(f"/issue/{issue_key}/transitions"),
            json=body,
        )
        return resp.status_code in (200, 204)


async def search_user(display_name: str) -> str | None:
    """按显示名搜索 JIRA 用户，返回 username/name（JIRA Server/DC 格式）"""
    async with _client() as c:
        resp = await c.get(
            _v2("/user/search"),
            params={"username": display_name, "maxResults": 5},
        )
        if resp.status_code != 200:
            return None
        users = resp.json()
        if isinstance(users, list) and users:
            return users[0].get("name") or users[0].get("key")
        return None


async def search_issues(jql: str, fields: list[str] | None = None) -> list[dict]:
    """用 JQL 搜索 issues（自动分页，拉取全部结果）"""
    all_issues: list[dict] = []
    start_at = 0
    page_size = 100
    field_str = ",".join(fields or ["summary", "status", "assignee", "comment"])
    async with _client() as c:
        while True:
            resp = await c.get(
                _v2("/search"),
                params={
                    "jql": jql,
                    "fields": field_str,
                    "maxResults": page_size,
                    "startAt": start_at,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            page = data.get("issues", [])
            all_issues.extend(page)
            total = data.get("total", 0)
            start_at += len(page)
            if start_at >= total or not page:
                break
    return all_issues


async def get_all_acms_issues() -> list[dict]:
    """获取 ACMS 项目所有 issue"""
    return await search_issues(
        jql=f"project = {settings.jira_project_key} ORDER BY created DESC",
        fields=["summary", "status", "assignee", "description", "comment", "created", "updated"],
    )


async def create_issue(summary: str, description: str, component: str | None = None) -> str:
    """在 JIRA 创建新 Bug issue，返回 issue key（如 ACMS-62）"""
    fields: dict = {
        "project":     {"key": settings.jira_project_key},
        "summary":     summary,
        "description": description,
        "issuetype":   {"name": "Bug"},
    }
    if component:
        fields["components"] = [{"name": component}]
    async with _client() as c:
        resp = await c.post(_v2("/issue"), json={"fields": fields})
        resp.raise_for_status()
        return resp.json()["key"]
