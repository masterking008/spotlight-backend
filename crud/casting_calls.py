from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, func
import uuid

import models
import schemas
from .audit import log_action


def _apply_deadline_status(casting_calls: list[models.CastingCall]) -> list[models.CastingCall]:
    """If an open call's deadline has passed, reflect it as closed in the response (no DB write)."""
    now = datetime.now(timezone.utc)
    for cc in casting_calls:
        if cc.status == "open" and cc.deadline:
            dl = cc.deadline if cc.deadline.tzinfo else cc.deadline.replace(tzinfo=timezone.utc)
            if now > dl:
                cc.status = "closed"
    return casting_calls


def _attach_application_counts(db: Session, casting_calls: list[models.CastingCall]) -> list[models.CastingCall]:
    if not casting_calls:
        return casting_calls
    ids = [cc.id for cc in casting_calls]
    counts = (
        db.query(models.Application.casting_call_id, func.count(models.Application.id).label("cnt"))
        .filter(models.Application.casting_call_id.in_(ids), models.Application.is_complete == True)
        .group_by(models.Application.casting_call_id)
        .all()
    )
    count_map = {row.casting_call_id: row.cnt for row in counts}
    for cc in casting_calls:
        cc.application_count = count_map.get(cc.id, 0)
    _apply_deadline_status(casting_calls)
    return casting_calls


def create_casting_call(db: Session, casting_call: schemas.CastingCallCreate, owner_id: str):
    data = casting_call.model_dump(exclude={"owner_id"})
    db_obj = models.CastingCall(**data, owner_id=owner_id)
    db_obj.update_slugs()
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    log_action(db, entity_type="casting_call", entity_id=db_obj.id,
               action="created", performed_by=owner_id, new_value={"title": db_obj.title})
    return db_obj


def get_casting_call(db: Session, casting_call_id: uuid.UUID, load_collaborators: bool = True) -> models.CastingCall | None:
    q = db.query(models.CastingCall)
    if load_collaborators:
        q = q.options(joinedload(models.CastingCall.collaborators))
    cc = q.filter(models.CastingCall.id == casting_call_id).first()
    if cc:
        _apply_deadline_status([cc])
    return cc


def get_casting_calls(
    db: Session,
    user_id: str,
    role: str,
    skip: int = 0,
    limit: int = 100,
) -> list[models.CastingCall]:
    query = db.query(models.CastingCall)
    if role == "admin":
        pass
    elif role == "casting_manager":
        collab_subq = (
            db.query(models.casting_call_collaborators.c.casting_call_id)
            .filter(models.casting_call_collaborators.c.user_id == user_id)
            .subquery()
        )
        query = query.filter(
            or_(
                models.CastingCall.owner_id == user_id,
                models.CastingCall.id.in_(collab_subq),
            )
        )
    results = query.order_by(models.CastingCall.created_at.desc()).offset(skip).limit(limit).all()
    return _attach_application_counts(db, results)


def update_casting_call(db: Session, casting_call_id: uuid.UUID, update: schemas.CastingCallUpdate, user_id: str):
    db_obj = get_casting_call(db, casting_call_id)
    if not db_obj:
        return None
    old = {"status": db_obj.status, "title": db_obj.title}
    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_obj, field, value)
    if 'show' in update_data or 'role' in update_data:
        db_obj.update_slugs()
    db_obj.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(db_obj)
    log_action(db, entity_type="casting_call", entity_id=db_obj.id,
               action="updated", performed_by=user_id, previous_value=old,
               new_value=update_data)
    return db_obj


def add_collaborator(db: Session, casting_call_id: uuid.UUID, user_id: str) -> bool:
    cc = get_casting_call(db, casting_call_id)
    if not cc:
        return False
    if any(c.id == user_id for c in cc.collaborators):
        return True
    user = db.query(models.UserProfile).filter(models.UserProfile.id == user_id).first()
    if not user:
        return False
    cc.collaborators.append(user)
    db.commit()
    return True


def remove_collaborator(db: Session, casting_call_id: uuid.UUID, user_id: str) -> bool:
    cc = get_casting_call(db, casting_call_id)
    if not cc:
        return False
    user = next((c for c in cc.collaborators if c.id == user_id), None)
    if not user:
        return False
    cc.collaborators.remove(user)
    db.commit()
    return True
