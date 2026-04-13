"""
Feishu OAuth 登录 + JWT 签发
GET  /auth/login           → 重定向到飞书授权页
GET  /auth/callback        → 飞书回调，换 token，签发 JWT
GET  /auth/me              → 当前用户信息
"""
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import jwt, JWTError

_bearer = HTTPBearer()

from app.database import get_db
from app.config import get_settings
from app.models.user import User, UserRole
from app.models.preset import UserRolePreset
from app.services import feishu as feishu_svc
from app.schemas.auth import TokenOut
from app.schemas.user import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()
logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30


def _create_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire},
        settings.secret_key,
        algorithm=JWT_ALGORITHM,
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """JWT 验证依赖，返回当前 User 对象"""
    try:
        payload = jwt.decode(
            credentials.credentials, settings.secret_key, algorithms=[JWT_ALGORITHM]
        )
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated")

    user.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return current_user


def require_pm_or_admin(current_user: User = Depends(get_current_user)) -> User:
    """PM（仅驾驶舱）或 Admin 均可访问"""
    if current_user.role not in (UserRole.admin, UserRole.pm):
        raise HTTPException(status_code=403, detail="PM or Admin only")
    return current_user


# ── 路由 ───────────────────────────────────────────────────────────

@router.get("/login")
def feishu_login(state: str = ""):
    """重定向到飞书 OAuth 授权页"""
    url = feishu_svc.get_oauth_url(state)
    return RedirectResponse(url)


@router.get("/callback")
async def feishu_callback(
    code: str = Query(...),
    state: str = Query(""),
    db: Session = Depends(get_db),
):
    """飞书 OAuth 回调：换 user_access_token，创建/更新用户，重定向到前端（?jwt=...）"""
    try:
        user_info = await feishu_svc.exchange_code(code)
    except Exception as e:
        logger.error("Feishu OAuth error: %s", e)
        raise HTTPException(status_code=400, detail="Feishu OAuth failed")

    feishu_uid = user_info.get("feishu_uid", "")
    if not feishu_uid:
        raise HTTPException(status_code=400, detail="Could not get feishu user id")

    feishu_name = user_info["feishu_name"]

    # 查预登记表
    preset = db.query(UserRolePreset).filter(
        UserRolePreset.feishu_name == feishu_name
    ).first()

    # 创建或更新用户
    user = db.query(User).filter(User.feishu_uid == feishu_uid).first()
    if not user:
        # 第一个注册的用户自动成为管理员；否则按预登记表；无登记则 pm
        is_first = db.query(User).count() == 0
        if is_first:
            role = UserRole.admin
        elif preset:
            role = preset.role
        else:
            role = UserRole.pm
        user = User(
            feishu_uid=feishu_uid,
            feishu_name=feishu_name,
            feishu_avatar=user_info.get("feishu_avatar"),
            role=role,
        )
        db.add(user)
    else:
        user.feishu_name = feishu_name
        user.feishu_avatar = user_info.get("feishu_avatar")
        # 预登记表更新即时生效（每次登录重新同步角色）
        if preset:
            user.role = preset.role

    user.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    token = _create_token(user.id)
    return RedirectResponse(url=f"/?jwt={token}")


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user
