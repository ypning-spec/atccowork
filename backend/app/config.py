from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_env: str = "development"
    secret_key: str = "dev-secret-key"
    base_url: str = "http://localhost:8000"

    database_url: str = "sqlite:///./data/acms.db"

    jira_base_url: str = ""
    jira_project_key: str = "ACMS"
    jira_user_email: str = ""
    jira_api_token: str = ""
    jira_webhook_secret: str = ""

    gitlab_base_url: str = ""
    gitlab_token: str = ""
    gitlab_project_path: str = ""
    gitlab_webhook_secret: str = ""

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_redirect_uri: str = "http://localhost:8000/auth/callback"

    llm_base_url: str = "https://api.anthropic.com/v1"
    llm_api_key: str = ""
    ai_model: str = "claude-sonnet-4-5-20250929"

    conversations_dir: str = "../data/conversations"

    # 本地代码仓库路径，用于 AI 分析前扫描真实代码，避免幻觉文件路径
    # 例：/Users/dev/myproject 或留空则跳过代码扫描
    codebase_path: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
