from datetime import datetime, timezone
from sqlalchemy.orm import Session

import models


def get_user_profile(db: Session, user_id: str) -> models.UserProfile | None:
    return db.query(models.UserProfile).filter(models.UserProfile.id == user_id).first()


def get_user_profile_by_email(db: Session, email: str) -> models.UserProfile | None:
    return db.query(models.UserProfile).filter(models.UserProfile.email == email).first()


def list_user_profiles(
    db: Session, skip: int = 0, limit: int = 200, active_only: bool = False
) -> list[models.UserProfile]:
    q = db.query(models.UserProfile)
    if active_only:
        q = q.filter(models.UserProfile.is_active == True)
    return q.order_by(models.UserProfile.email).offset(skip).limit(limit).all()


def update_user_role(db: Session, user_id: str, role: str) -> models.UserProfile | None:
    profile = get_user_profile(db, user_id)
    if not profile:
        return None
    profile.role = role
    profile.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(profile)
    return profile


def update_user_profile(db: Session, user_id: str, updates: dict) -> models.UserProfile | None:
    profile = get_user_profile(db, user_id)
    if not profile:
        return None
    for key, val in updates.items():
        if hasattr(profile, key) and val is not None:
            setattr(profile, key, val)
    profile.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(profile)
    return profile


def upsert_user_role(db: Session, user_id: str, email: str, role: str) -> models.UserProfile:
    profile = get_user_profile(db, user_id)
    if profile:
        profile.role = role
        profile.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(profile)
        return profile
    obj = models.UserProfile(id=user_id, email=email, role=role)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# Backwards-compat aliases used by rbac.py
def get_user_role(db: Session, user_id: str) -> models.UserProfile | None:
    return get_user_profile(db, user_id)


def list_user_roles(db: Session, skip: int = 0, limit: int = 200) -> list[models.UserProfile]:
    return list_user_profiles(db, skip, limit)
