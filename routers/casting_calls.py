import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from rbac import require_role, require_any_role, assert_can_manage
import models, schemas, crud

router = APIRouter()


@router.post("/casting-calls", response_model=schemas.CastingCall)
async def create_casting_call(
    body: schemas.CastingCallCreate,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    return crud.create_casting_call(db, body, owner_id=current_user["id"])


@router.get("/casting-calls", response_model=List[schemas.CastingCall])
async def list_casting_calls(
    skip: int = 0,
    limit: int = 100,
    current_user: dict = Depends(require_any_role()),
    db: Session = Depends(get_db),
):
    return crud.get_casting_calls(
        db, user_id=current_user["id"], role=current_user["app_role"], skip=skip, limit=limit
    )


@router.get("/casting-calls/{casting_call_id}", response_model=schemas.CastingCall)
async def get_casting_call(
    casting_call_id: uuid.UUID,
    current_user: dict = Depends(require_any_role()),
    db: Session = Depends(get_db),
):
    cc = crud.get_casting_call(db, casting_call_id)
    if not cc:
        raise HTTPException(404, "Casting call not found")
    return cc


@router.get("/casting-calls/{casting_call_id}/public")
async def get_public_casting_call(casting_call_id: uuid.UUID, db: Session = Depends(get_db)):
    """Public endpoint — no auth required. Used by talent application form."""
    cc = crud.get_casting_call(db, casting_call_id)
    if not cc or cc.status != "open":
        raise HTTPException(404, "Casting call not found or not currently accepting applications")
    return {
        "id": cc.id,
        "title": cc.title,
        "show": cc.show,
        "role": cc.role,
        "description": cc.description,
        "deadline": cc.deadline,
        "form_schema": cc.form_schema,
        "banner_url": cc.banner_url,
    }


@router.get("/casting-calls/by-slug/{show_slug}/{role_slug}")
async def get_casting_call_by_slug(show_slug: str, role_slug: str, db: Session = Depends(get_db)):
    """Public endpoint — get casting call by show and role slugs. No auth required."""
    cc = db.query(models.CastingCall).filter(
        models.CastingCall.show_slug == show_slug,
        models.CastingCall.role_slug == role_slug,
        models.CastingCall.status == "open"
    ).first()

    if not cc:
        raise HTTPException(404, "Casting call not found or not currently accepting applications")

    return {
        "id": cc.id,
        "title": cc.title,
        "show": cc.show,
        "role": cc.role,
        "description": cc.description,
        "deadline": cc.deadline,
        "form_schema": cc.form_schema,
        "banner_url": cc.banner_url,
    }


@router.put("/casting-calls/{casting_call_id}", response_model=schemas.CastingCall)
async def update_casting_call(
    casting_call_id: uuid.UUID,
    body: schemas.CastingCallUpdate,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    cc = crud.get_casting_call(db, casting_call_id)
    if not cc:
        raise HTTPException(404, "Casting call not found")
    assert_can_manage(current_user, cc)
    updated = crud.update_casting_call(db, casting_call_id, body, user_id=current_user["id"])
    return updated


# ─── Collaborators ────────────────────────────────────────────────────────────

@router.get("/casting-calls/{casting_call_id}/collaborators", response_model=List[schemas.CollaboratorOut])
async def list_collaborators(
    casting_call_id: uuid.UUID,
    current_user: dict = Depends(require_any_role()),
    db: Session = Depends(get_db),
):
    cc = crud.get_casting_call(db, casting_call_id)
    if not cc:
        raise HTTPException(404, "Casting call not found")
    return cc.collaborators


@router.post("/casting-calls/{casting_call_id}/collaborators", response_model=schemas.CastingCall)
async def add_collaborator(
    casting_call_id: uuid.UUID,
    body: schemas.CollaboratorAdd,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    cc = crud.get_casting_call(db, casting_call_id)
    if not cc:
        raise HTTPException(404, "Casting call not found")
    assert_can_manage(current_user, cc)
    # Only casting_managers and admins can be co-managers
    target = db.query(models.UserProfile).filter(models.UserProfile.id == body.user_id).first()
    if not target:
        raise HTTPException(404, "User not found")
    if target.role not in ("casting_manager", "admin"):
        raise HTTPException(400, "Only casting managers or admins can be co-managers")
    if cc.owner_id == body.user_id:
        raise HTTPException(400, "Owner is already the manager of this casting call")
    crud.add_collaborator(db, casting_call_id, body.user_id)
    return crud.get_casting_call(db, casting_call_id)


@router.delete("/casting-calls/{casting_call_id}/collaborators/{user_id}", response_model=schemas.CastingCall)
async def remove_collaborator(
    casting_call_id: uuid.UUID,
    user_id: str,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    cc = crud.get_casting_call(db, casting_call_id)
    if not cc:
        raise HTTPException(404, "Casting call not found")
    assert_can_manage(current_user, cc)
    removed = crud.remove_collaborator(db, casting_call_id, user_id)
    if not removed:
        raise HTTPException(404, "Collaborator not found")
    return crud.get_casting_call(db, casting_call_id)
