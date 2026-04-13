"""飞书 OAuth 2.0 登录封装"""
import httpx
from urllib.parse import urlencode, quote
from app.config import get_settings

settings = get_settings()

FEISHU_AUTH_URL      = "https://open.feishu.cn/open-apis/authen/v1/authorize"
FEISHU_TOKEN_URL     = "https://open.feishu.cn/open-apis/authen/v1/access_token"
FEISHU_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"


def get_oauth_url(state: str = "") -> str:
    """返回飞书 OAuth 授权跳转 URL（不携带额外 scope，避免权限审批）"""
    params = urlencode({
        "app_id":       settings.feishu_app_id,
        "redirect_uri": settings.feishu_redirect_uri,
        "state":        state,
    }, quote_via=quote)
    return f"{FEISHU_AUTH_URL}?{params}"


async def _get_app_access_token() -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FEISHU_APP_TOKEN_URL,
            json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        )
        resp.raise_for_status()
        return resp.json()["tenant_access_token"]


async def exchange_code(code: str) -> dict:
    """用 OAuth code 换取 user_access_token，响应里直接含用户信息"""
    app_token = await _get_app_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FEISHU_TOKEN_URL,
            headers={
                "Authorization": f"Bearer {app_token}",
                "Content-Type": "application/json",
            },
            json={"grant_type": "authorization_code", "code": code},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        # /authen/v1/access_token 响应里直接含 open_id / name / avatar_url
        return {
            "access_token":  data.get("access_token", ""),
            "feishu_uid":    data.get("open_id", ""),
            "feishu_name":   data.get("name", ""),
            "feishu_avatar": data.get("avatar_url", ""),
        }
