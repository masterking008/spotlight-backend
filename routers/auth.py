from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from auth import get_current_user, make_internal_user_dep
from rbac import require_role
import schemas, crud, otp as otp_svc

router = APIRouter()

# Internal user dep — validates JWT + enforces @ruskmedia.com + auto-provisions profile
get_internal_user = make_internal_user_dep(get_db)


# ─── OTP ─────────────────────────────────────────────────────────────────────

@router.post("/otp/send", status_code=200)
async def send_otp(body: schemas.OTPRequest, db: Session = Depends(get_db)):
    try:
        otp_svc.send_otp(db, body.phone, body.purpose)
    except otp_svc.OTPError as e:
        raise HTTPException(status_code=429, detail=str(e))
    return {"message": "OTP sent successfully"}


@router.post("/otp/verify", response_model=schemas.OTPVerifyResponse)
async def verify_otp(body: schemas.OTPVerify, db: Session = Depends(get_db)):
    try:
        token = otp_svc.verify_otp_and_issue_token(db, body.phone, body.otp, body.purpose)
    except otp_svc.OTPError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return schemas.OTPVerifyResponse(phone_token=token)


# ─── Auth / Profile ──────────────────────────────────────────────────────────

@router.get("/me/role")
async def get_my_role(
    current_user: dict = Depends(get_internal_user),
    db: Session = Depends(get_db),
):
    """Returns the caller's app role. Also triggers profile auto-provision."""
    profile = crud.get_user_profile(db, current_user["id"])
    if not profile:
        return {"role": None, "id": current_user["id"], "email": current_user.get("email")}
    return {
        "role": profile.role,
        "id": profile.id,
        "email": profile.email,
        "full_name": profile.full_name,
        "is_active": profile.is_active,
    }


@router.get("/me/profile", response_model=schemas.UserProfileOut)
async def get_my_profile(
    current_user: dict = Depends(get_internal_user),
    db: Session = Depends(get_db),
):
    profile = crud.get_user_profile(db, current_user["id"])
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.patch("/me/profile", response_model=schemas.UserProfileOut)
async def update_my_profile(
    body: schemas.UserProfileUpdate,
    current_user: dict = Depends(get_internal_user),
    db: Session = Depends(get_db),
):
    """Users can update their own non-role fields."""
    updates = body.model_dump(exclude_none=True)
    updates.pop("is_active", None)
    profile = crud.update_user_profile(db, current_user["id"], updates)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.get("/admin/users", response_model=List[schemas.UserProfileOut])
async def list_users(
    skip: int = 0,
    limit: int = 200,
    active_only: bool = False,
    current_user: dict = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    return crud.list_user_profiles(db, skip=skip, limit=limit, active_only=active_only)


@router.get("/admin/users/{user_id}", response_model=schemas.UserProfileOut)
async def get_user(
    user_id: str,
    current_user: dict = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    profile = crud.get_user_profile(db, user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="User not found")
    return profile


@router.put("/admin/users/{user_id}/role", response_model=schemas.UserProfileOut)
async def assign_role(
    user_id: str,
    body: schemas.UserRoleAssign,
    current_user: dict = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    profile = crud.update_user_role(db, user_id=user_id, role=body.role.value)
    if not profile:
        raise HTTPException(status_code=404, detail="User not found — they must sign in first")
    return profile


@router.patch("/admin/users/{user_id}", response_model=schemas.UserProfileOut)
async def admin_update_profile(
    user_id: str,
    body: schemas.UserProfileUpdate,
    current_user: dict = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin can update any profile field including is_active."""
    updates = body.model_dump(exclude_none=True)
    profile = crud.update_user_profile(db, user_id, updates)
    if not profile:
        raise HTTPException(status_code=404, detail="User not found")
    return profile
