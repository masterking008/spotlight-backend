from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_
import uuid

import models
import schemas
import storage as storage_svc
from .audit import log_action


# ─── Applicants ───────────────────────────────────────────────────────────────

def get_or_create_applicant(db: Session, data: schemas.ApplicantCreate) -> models.Applicant:
    existing = db.query(models.Applicant).filter(models.Applicant.phone == data.phone).first()
    if not existing:
        db_obj = models.Applicant(**data.model_dump())
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is not None and field != "phone":
            setattr(existing, field, value)
    db.commit()
    db.refresh(existing)
    return existing


# ─── Applications ─────────────────────────────────────────────────────────────

def _enrich_application(db: Session, app: models.Application) -> models.Application:
    for media in app.media:
        if media.storage_path:
            try:
                media.url = storage_svc.create_signed_read_url(media.storage_path)
            except Exception:
                pass
    return app


def create_application(db: Session, application: schemas.ApplicationCreate) -> models.Application:
    db_applicant = get_or_create_applicant(db, application.applicant)
    db_obj = models.Application(
        casting_call_id=application.casting_call_id,
        applicant_id=db_applicant.id,
        custom_responses=application.custom_responses,
        consent_given=application.consent_given,
        tracking_id=str(uuid.uuid4())[:8].upper(),
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    log_action(db, entity_type="application", entity_id=db_obj.id,
               action="submitted", performed_by=db_applicant.phone,
               new_value={"status": "new", "tracking_id": db_obj.tracking_id})
    return db_obj


def get_application(db: Session, application_id: uuid.UUID) -> models.Application | None:
    return (
        db.query(models.Application)
        .options(
            joinedload(models.Application.applicant),
            joinedload(models.Application.media),
            joinedload(models.Application.tags),
            joinedload(models.Application.casting_call),
        )
        .filter(models.Application.id == application_id)
        .first()
    )


def get_applications_by_casting_call(
    db: Session,
    casting_call_id: uuid.UUID,
    *,
    status_filter: list[str] = None,
    tag_filter: list[str] = None,
    search: str = None,
    sort: str = "submitted_at_desc",
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[models.Application], int]:
    query = (
        db.query(models.Application)
        .options(
            joinedload(models.Application.applicant),
            joinedload(models.Application.media),
            joinedload(models.Application.tags),
        )
        .filter(
            models.Application.casting_call_id == casting_call_id,
            models.Application.is_complete == True,  # noqa: E712
        )
    )

    if status_filter:
        query = query.filter(models.Application.status.in_(status_filter))

    if search:
        query = query.join(models.Applicant).filter(
            or_(
                models.Applicant.name.ilike(f"%{search}%"),
                models.Applicant.phone.contains(search),
                models.Applicant.city.ilike(f"%{search}%"),
            )
        )

    if tag_filter:
        for tag in tag_filter:
            query = query.filter(
                models.Application.id.in_(
                    db.query(models.ApplicationTag.application_id).filter(
                        models.ApplicationTag.tag_name == tag
                    )
                )
            )

    total = query.count()

    if sort == "submitted_at_asc":
        query = query.order_by(models.Application.submitted_at.asc())
    elif sort == "name_asc":
        query = query.join(models.Applicant).order_by(models.Applicant.name.asc())
    else:
        query = query.order_by(models.Application.submitted_at.desc())

    apps = query.offset(skip).limit(limit).all()
    for app in apps:
        _enrich_application(db, app)
    return apps, total


def get_all_applications(
    db: Session,
    *,
    search: str = None,
    status_filter: list[str] = None,
    casting_call_id: Optional[uuid.UUID] = None,
    sort: str = "submitted_at_desc",
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[models.Application], int]:
    query = (
        db.query(models.Application)
        .options(
            joinedload(models.Application.applicant),
            joinedload(models.Application.media),
            joinedload(models.Application.tags),
        )
        .join(models.Applicant)
        .join(models.CastingCall)
        .filter(models.Application.is_complete == True)  # noqa: E712
    )

    if casting_call_id:
        query = query.filter(models.Application.casting_call_id == casting_call_id)

    if status_filter:
        query = query.filter(models.Application.status.in_(status_filter))

    if search:
        query = query.filter(
            or_(
                models.Applicant.name.ilike(f"%{search}%"),
                models.Applicant.phone.contains(search),
                models.Applicant.city.ilike(f"%{search}%"),
                models.CastingCall.show.ilike(f"%{search}%"),
                models.CastingCall.title.ilike(f"%{search}%"),
                models.CastingCall.role.ilike(f"%{search}%"),
            )
        )

    if sort == "submitted_at_asc":
        query = query.order_by(models.Application.submitted_at.asc())
    elif sort == "name_asc":
        query = query.order_by(models.Applicant.name.asc())
    else:
        query = query.order_by(models.Application.submitted_at.desc())

    total = query.count()
    apps = query.offset(skip).limit(limit).all()
    for app in apps:
        _enrich_application(db, app)
    return apps, total


def search_applications_global(
    db: Session, q: str, skip: int = 0, limit: int = 50
) -> tuple[list[models.Application], int]:
    return get_all_applications(db, search=q, skip=skip, limit=limit)


def get_application_by_tracking(db: Session, tracking_id: str) -> models.Application | None:
    return (
        db.query(models.Application)
        .options(
            joinedload(models.Application.applicant),
            joinedload(models.Application.casting_call),
        )
        .filter(models.Application.tracking_id == tracking_id)
        .first()
    )


def update_application_status(
    db: Session, application_id: uuid.UUID, new_status: str, user_id: str
) -> models.Application | None:
    db_obj = get_application(db, application_id)
    if not db_obj:
        return None
    old_status = db_obj.status
    db_obj.status = new_status
    db_obj.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(db_obj)
    log_action(db, entity_type="application", entity_id=application_id,
               action="status_changed", performed_by=user_id,
               previous_value={"status": old_status},
               new_value={"status": new_status})
    return db_obj


def update_application_notes(db: Session, application_id: uuid.UUID, notes: str, user_id: str):
    db_obj = db.query(models.Application).filter(models.Application.id == application_id).first()
    if not db_obj:
        return None
    db_obj.notes = notes
    db_obj.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def get_shortlisted_applications(db: Session, casting_call_id: uuid.UUID) -> list[models.Application]:
    apps = (
        db.query(models.Application)
        .options(
            joinedload(models.Application.applicant),
            joinedload(models.Application.media),
            joinedload(models.Application.tags),
        )
        .filter(
            and_(
                models.Application.casting_call_id == casting_call_id,
                models.Application.status.in_(["shortlisted", "pitched"]),
            )
        )
        .order_by(models.Application.updated_at.desc())
        .all()
    )
    for app in apps:
        _enrich_application(db, app)
    return apps


# ─── Tags ─────────────────────────────────────────────────────────────────────

def add_application_tag(db: Session, application_id: uuid.UUID, tag: schemas.TagCreate, user_id: str):
    existing = db.query(models.ApplicationTag).filter(
        and_(
            models.ApplicationTag.application_id == application_id,
            models.ApplicationTag.tag_name == tag.tag_name,
        )
    ).first()
    if existing:
        return existing
    db_obj = models.ApplicationTag(
        application_id=application_id,
        tag_name=tag.tag_name,
        applied_by=user_id,
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    log_action(db, entity_type="application", entity_id=application_id,
               action="tag_added", performed_by=user_id,
               new_value={"tag": tag.tag_name})
    return db_obj


def get_application_tags(db: Session, application_id: uuid.UUID) -> list[models.ApplicationTag]:
    return db.query(models.ApplicationTag).filter(
        models.ApplicationTag.application_id == application_id
    ).all()


def remove_application_tag(db: Session, application_id: uuid.UUID, tag_name: str, user_id: str) -> bool:
    db_obj = db.query(models.ApplicationTag).filter(
        and_(
            models.ApplicationTag.application_id == application_id,
            models.ApplicationTag.tag_name == tag_name,
        )
    ).first()
    if not db_obj:
        return False
    db.delete(db_obj)
    db.commit()
    log_action(db, entity_type="application", entity_id=application_id,
               action="tag_removed", performed_by=user_id,
               previous_value={"tag": tag_name})
    return True
