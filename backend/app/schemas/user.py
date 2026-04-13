from pydantic import BaseModel
from datetime import datetime
from app.models.user import UserRole


class UserOut(BaseModel):
    id: int
    feishu_uid: str
    feishu_name: str
    feishu_avatar: str | None
    role: UserRole
    is_active: bool
    created_at: datetime
    last_seen_at: datetime | None

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
