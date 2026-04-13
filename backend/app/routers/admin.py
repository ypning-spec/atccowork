"""
Admin 路由：预登记表 CRUD
仅 admin 角色可访问。

GET    /admin/presets         列出全部预登记
POST   /admin/presets         新增 / 覆盖一条（按 feishu_name 唯一）
PUT    /admin/presets/{id}    修改（role / note）
DELETE /admin/presets/{id}    删除
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models.preset import UserRolePreset
from app.models.user import UserRole
from app.routers.auth import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Schemas ────────────────────────────────────────────────

class PresetOut(BaseModel):
    id: int
    feishu_name: str
    role: UserRole
    note: str | None

    class Config:
        from_attributes = True


class PresetCreate(BaseModel):
    feishu_name: str
    role: UserRole
    note: str | None = None


class PresetUpdate(BaseModel):
    role: UserRole | None = None
    note: str | None = None


# ── Routes ────────────────────────────────────────────────

@router.get("/presets", response_model=list[PresetOut])
def list_presets(
    db: Session = Depends(get_db),
    _: object = Depends(require_admin),
):
    return db.query(UserRolePreset).order_by(UserRolePreset.feishu_name).all()


@router.post("/presets", response_model=PresetOut)
def create_or_update_preset(
    body: PresetCreate,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin),
):
    """按 feishu_name 唯一：已存在则覆盖 role/note，否则新增。"""
    existing = db.query(UserRolePreset).filter(
        UserRolePreset.feishu_name == body.feishu_name
    ).first()
    if existing:
        existing.role = body.role
        existing.note = body.note
        db.commit()
        db.refresh(existing)
        return existing

    preset = UserRolePreset(
        feishu_name=body.feishu_name,
        role=body.role,
        note=body.note,
    )
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return preset


@router.put("/presets/{preset_id}", response_model=PresetOut)
def update_preset(
    preset_id: int,
    body: PresetUpdate,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin),
):
    preset = db.query(UserRolePreset).filter(UserRolePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    if body.role is not None:
        preset.role = body.role
    if body.note is not None:
        preset.note = body.note
    db.commit()
    db.refresh(preset)
    return preset


@router.delete("/presets/{preset_id}")
def delete_preset(
    preset_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin),
):
    preset = db.query(UserRolePreset).filter(UserRolePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    db.delete(preset)
    db.commit()
    return {"ok": True}
