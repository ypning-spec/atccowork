from pydantic import BaseModel


class FeishuCallbackIn(BaseModel):
    code: str
    state: str = ""


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    feishu_name: str
    feishu_avatar: str
    role: str
