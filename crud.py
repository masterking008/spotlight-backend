from typing import Optional, List
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from datetime import datetime, timezone, date
import uuid
import decimal

import models
import schemas
import storage as storage_svc


# ─── JSON-safe serialiser ─────────────────────────────────────────────────────

def _json_safe(obj):
    """
    Recursively convert a dict/list so it can be stored in a JSON column.
    Handles: datetime → ISO string, date → ISO string, Decimal → float,
             enums → .value, and anything else → str().
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if hasattr(obj, 'value'):          # enum
        return obj.value
    if isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


# ─── Audit ────────────────────────────────────────────────────────────────────

def log_action(
    db: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    performed_by: str,
    previous_value: dict = None,
    new_value: dict = None,
):
    entry = models.AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        performed_by=performed_by,
        previous_value=_json_safe(previous_value),
        new_value=_json_safe(new_value),
    )
    db.add(entry)
    db.commit()
    return entry


# ─── User Profiles ─────────────────────────────────────────────────────────────

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


def update_user_profile(
    db: Session, user_id: str, updates: dict
) -> models.UserProfile | None:
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


# Backwards-compat aliases used by rbac.py and any remaining references
def get_user_role(db: Session, user_id: str) -> models.UserProfile | None:
    return get_user_profile(db, user_id)


def list_user_roles(db: Session, skip: int = 0, limit: int = 200) -> list[models.UserProfile]:
    return list_user_profiles(db, skip, limit)


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


# ─── Casting Calls ─────────────────────────────────────────────────────────────

def create_casting_call(db: Session, casting_call: schemas.CastingCallCreate, owner_id: str):
    data = casting_call.model_dump(exclude={"owner_id"})
    db_obj = models.CastingCall(**data, owner_id=owner_id)
    # Generate slugs after object creation
    db_obj.update_slugs()
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    log_action(db, entity_type="casting_call", entity_id=db_obj.id,
               action="created", performed_by=owner_id, new_value={"title": db_obj.title})
    return db_obj


def get_casting_call(db: Session, casting_call_id: uuid.UUID) -> models.CastingCall | None:
    return db.query(models.CastingCall).filter(models.CastingCall.id == casting_call_id).first()


def get_casting_calls(
    db: Session,
    user_id: str,
    role: str,
    skip: int = 0,
    limit: int = 100,
) -> list[models.CastingCall]:
    query = db.query(models.CastingCall)
    if role == "admin":
        pass  # see all
    elif role == "casting_manager":
        # owned OR collaborating
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
    else:
        # approver/viewer — see all (read-only enforced at endpoint level)
        pass
    return query.order_by(models.CastingCall.created_at.desc()).offset(skip).limit(limit).all()


def add_collaborator(db: Session, casting_call_id: uuid.UUID, user_id: str) -> bool:
    """Add a co-manager to a casting call. Returns False if already present."""
    cc = get_casting_call(db, casting_call_id)
    if not cc:
        return False
    if any(c.id == user_id for c in cc.collaborators):
        return True  # already there, idempotent
    user = db.query(models.UserProfile).filter(models.UserProfile.id == user_id).first()
    if not user:
        return False
    cc.collaborators.append(user)
    db.commit()
    return True


def remove_collaborator(db: Session, casting_call_id: uuid.UUID, user_id: str) -> bool:
    """Remove a co-manager from a casting call. Returns False if not found."""
    cc = get_casting_call(db, casting_call_id)
    if not cc:
        return False
    user = next((c for c in cc.collaborators if c.id == user_id), None)
    if not user:
        return False
    cc.collaborators.remove(user)
    db.commit()
    return True


def update_casting_call(db: Session, casting_call_id: uuid.UUID, update: schemas.CastingCallUpdate, user_id: str):
    db_obj = get_casting_call(db, casting_call_id)
    if not db_obj:
        return None
    old = {"status": db_obj.status, "title": db_obj.title}
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(db_obj, field, value)
    # Update slugs if show or role changed
    if 'show' in update.model_dump(exclude_unset=True) or 'role' in update.model_dump(exclude_unset=True):
        db_obj.update_slugs()
    db_obj.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(db_obj)
    log_action(db, entity_type="casting_call", entity_id=db_obj.id,
               action="updated", performed_by=user_id, previous_value=old,
               new_value=update.model_dump(exclude_unset=True))
    return db_obj


# ─── Applicants ────────────────────────────────────────────────────────────────

def get_or_create_applicant(db: Session, data: schemas.ApplicantCreate) -> models.Applicant:
    existing = db.query(models.Applicant).filter(models.Applicant.phone == data.phone).first()
    if not existing:
        db_obj = models.Applicant(**data.model_dump())
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj
    # Update profile fields that may have improved
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is not None and field != "phone":
            setattr(existing, field, value)
    db.commit()
    db.refresh(existing)
    return existing


# ─── Applications ──────────────────────────────────────────────────────────────

def _enrich_application(db: Session, app: models.Application) -> models.Application:
    """Attach fresh signed media URLs to an application's media list."""
    for media in app.media:
        if media.storage_path:
            try:
                media.url = storage_svc.create_signed_read_url(media.storage_path)
            except Exception:
                pass  # leave existing url if signing fails
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
    """List all applications across every casting call, with optional filters."""
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
    db: Session,
    q: str,
    skip: int = 0,
    limit: int = 50,
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


# ─── Tags ──────────────────────────────────────────────────────────────────────

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


# ─── Media ─────────────────────────────────────────────────────────────────────

def create_upload_session(
    db: Session,
    application_id: uuid.UUID,
    storage_path: str,
    filename: str,
    file_size: int,
    media_type: str,
    upload_url: str = None,
) -> models.UploadSession:
    from datetime import timedelta
    session = models.UploadSession(
        id=str(uuid.uuid4()),
        application_id=application_id,
        filename=filename,
        file_size=file_size,
        media_type=media_type,
        storage_path=storage_path,
        upload_url=upload_url,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def complete_upload_session(
    db: Session, session_id: str, application_id: uuid.UUID
) -> models.ApplicationMedia | None:
    session = db.query(models.UploadSession).filter(
        and_(
            models.UploadSession.id == session_id,
            models.UploadSession.application_id == application_id,
        )
    ).first()
    if not session:
        return None
    session.completed = True
    media = models.ApplicationMedia(
        application_id=application_id,
        type=session.media_type,
        storage_path=session.storage_path,
        filename=session.filename,
        file_size=session.file_size,
        upload_status="complete",
    )
    db.add(media)
    db.commit()
    db.refresh(media)
    # Attach fresh signed URL
    try:
        media.url = storage_svc.create_signed_read_url(media.storage_path)
    except Exception:
        pass
    return media


def get_application_media(db: Session, application_id: uuid.UUID) -> list[models.ApplicationMedia]:
    media_list = db.query(models.ApplicationMedia).filter(
        and_(
            models.ApplicationMedia.application_id == application_id,
            models.ApplicationMedia.upload_status == "complete",
        )
    ).all()
    for media in media_list:
        if media.storage_path:
            try:
                media.url = storage_svc.create_signed_read_url(media.storage_path)
            except Exception:
                pass
    return media_list


def delete_media(db: Session, media_id: uuid.UUID, application_id: uuid.UUID) -> bool:
    media = db.query(models.ApplicationMedia).filter(
        and_(
            models.ApplicationMedia.id == media_id,
            models.ApplicationMedia.application_id == application_id,
        )
    ).first()
    if not media:
        return False
    if media.storage_path:
        storage_svc.delete_object(media.storage_path)
    db.delete(media)
    db.commit()
    return True


# ─── Pitch Decks ──────────────────────────────────────────────────────────────

def create_pitch_deck(db: Session, data: schemas.PitchDeckCreate, user_id: str) -> models.PitchDeck:
    deck = models.PitchDeck(
        casting_call_id=data.casting_call_id,
        created_by=user_id,
        title=data.title,
        notes=data.notes,
    )
    db.add(deck)
    db.commit()
    db.refresh(deck)
    log_action(db, entity_type="pitch_deck", entity_id=deck.id,
               action="created", performed_by=user_id, new_value={"title": deck.title})
    return deck


def get_pitch_deck(db: Session, deck_id: uuid.UUID) -> models.PitchDeck | None:
    return (
        db.query(models.PitchDeck)
        .options(
            joinedload(models.PitchDeck.finalists).joinedload(
                models.PitchDeckFinalist.application
            ).joinedload(models.Application.applicant),
            joinedload(models.PitchDeck.finalists).joinedload(
                models.PitchDeckFinalist.application
            ).joinedload(models.Application.media),
            joinedload(models.PitchDeck.casting_call),
        )
        .filter(models.PitchDeck.id == deck_id)
        .first()
    )


def get_pitch_decks_for_call(db: Session, casting_call_id: uuid.UUID) -> list[models.PitchDeck]:
    return (
        db.query(models.PitchDeck)
        .filter(models.PitchDeck.casting_call_id == casting_call_id)
        .order_by(models.PitchDeck.created_at.desc())
        .all()
    )


def get_submitted_decks_for_approver(db: Session) -> list[models.PitchDeck]:
    return (
        db.query(models.PitchDeck)
        .options(joinedload(models.PitchDeck.casting_call))
        .filter(models.PitchDeck.status.in_(["submitted", "changes_requested"]))
        .order_by(models.PitchDeck.submitted_at.desc())
        .all()
    )


def update_pitch_deck(db: Session, deck_id: uuid.UUID, data: schemas.PitchDeckUpdate, user_id: str):
    deck = db.query(models.PitchDeck).filter(models.PitchDeck.id == deck_id).first()
    if not deck:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(deck, field, value)
    deck.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(deck)
    return deck


def add_deck_finalist(db: Session, deck_id: uuid.UUID, data: schemas.FinalistCreate) -> models.PitchDeckFinalist:
    finalist = models.PitchDeckFinalist(
        deck_id=deck_id,
        application_id=data.application_id,
        position=data.position,
        manager_notes=data.manager_notes,
        approver_verdict="pending",
    )
    db.add(finalist)
    db.commit()
    db.refresh(finalist)
    return finalist


def update_deck_finalist(db: Session, finalist_id: uuid.UUID, data: schemas.FinalistUpdate):
    finalist = db.query(models.PitchDeckFinalist).filter(
        models.PitchDeckFinalist.id == finalist_id
    ).first()
    if not finalist:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(finalist, field, value)
    db.commit()
    db.refresh(finalist)
    return finalist


def remove_deck_finalist(db: Session, finalist_id: uuid.UUID, deck_id: uuid.UUID) -> bool:
    finalist = db.query(models.PitchDeckFinalist).filter(
        and_(
            models.PitchDeckFinalist.id == finalist_id,
            models.PitchDeckFinalist.deck_id == deck_id,
        )
    ).first()
    if not finalist:
        return False
    db.delete(finalist)
    db.commit()
    return True


def submit_pitch_deck(db: Session, deck: models.PitchDeck, user_id: str) -> models.PitchDeck:
    deck.status = "submitted"
    deck.submitted_at = datetime.now(timezone.utc)
    deck.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(deck)
    log_action(db, entity_type="pitch_deck", entity_id=deck.id,
               action="submitted", performed_by=user_id)
    return deck


def set_deck_verdict(
    db: Session, deck: models.PitchDeck, action: str, reviewer_id: str, notes: str = None
) -> models.PitchDeck:
    deck.status = action
    deck.reviewer_id = reviewer_id
    deck.reviewer_notes = notes
    deck.reviewed_at = datetime.now(timezone.utc)
    deck.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(deck)
    log_action(db, entity_type="pitch_deck", entity_id=deck.id,
               action=action, performed_by=reviewer_id,
               new_value={"notes": notes})
    return deck


def set_finalist_verdict(
    db: Session, finalist_id: uuid.UUID, verdict: str, notes: str, reviewer_id: str
) -> models.PitchDeckFinalist | None:
    finalist = db.query(models.PitchDeckFinalist).filter(
        models.PitchDeckFinalist.id == finalist_id
    ).first()
    if not finalist:
        return None
    finalist.approver_verdict = verdict
    finalist.approver_notes = notes
    db.commit()
    db.refresh(finalist)
    log_action(db, entity_type="pitch_deck", entity_id=finalist.deck_id,
               action=f"finalist_{verdict}", performed_by=reviewer_id,
               new_value={"finalist_id": finalist_id, "verdict": verdict})
    return finalist


# ─── Audit Log ────────────────────────────────────────────────────────────────

def get_audit_log(
    db: Session, entity_type: str, entity_id: int, limit: int = 50
) -> list[models.AuditLog]:
    return (
        db.query(models.AuditLog)
        .filter(
            and_(
                models.AuditLog.entity_type == entity_type,
                models.AuditLog.entity_id == entity_id,
            )
        )
        .order_by(models.AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )


# ─── In-App Notifications ─────────────────────────────────────────────────────

def get_in_app_notifications(
    db: Session, user_id: str, skip: int = 0, limit: int = 30
) -> list[models.InAppNotification]:
    return (
        db.query(models.InAppNotification)
        .filter(models.InAppNotification.user_id == user_id)
        .order_by(models.InAppNotification.created_at.desc())
        .offset(skip).limit(limit)
        .all()
    )


def get_unread_count(db: Session, user_id: str) -> int:
    return db.query(models.InAppNotification).filter(
        and_(
            models.InAppNotification.user_id == user_id,
            models.InAppNotification.read == False,
        )
    ).count()


def mark_notification_read(db: Session, notification_id: uuid.UUID, user_id: str) -> bool:
    notif = db.query(models.InAppNotification).filter(
        and_(
            models.InAppNotification.id == notification_id,
            models.InAppNotification.user_id == user_id,
        )
    ).first()
    if not notif:
        return False
    notif.read = True
    db.commit()
    return True


def mark_all_read(db: Session, user_id: str):
    db.query(models.InAppNotification).filter(
        and_(
            models.InAppNotification.user_id == user_id,
            models.InAppNotification.read == False,
        )
    ).update({"read": True})
    db.commit()


# ─── Analytics ────────────────────────────────────────────────────────────────

def get_overview(db: Session, user_id: str, role: str) -> dict:
    # Scope to owned + collaborating calls for casting_manager
    cc_query = db.query(models.CastingCall)
    if role == "casting_manager":
        collab_subq = (
            db.query(models.casting_call_collaborators.c.casting_call_id)
            .filter(models.casting_call_collaborators.c.user_id == user_id)
            .subquery()
        )
        cc_query = cc_query.filter(
            or_(
                models.CastingCall.owner_id == user_id,
                models.CastingCall.id.in_(collab_subq),
            )
        )

    total_calls = cc_query.count()
    open_calls  = cc_query.filter(models.CastingCall.status == "open").count()
    draft_calls = cc_query.filter(models.CastingCall.status == "draft").count()

    scoped_call_ids = [r.id for r in cc_query.with_entities(models.CastingCall.id).all()]

    app_query = db.query(models.Application)
    if role == "casting_manager":
        app_query = app_query.filter(models.Application.casting_call_id.in_(scoped_call_ids))

    total_applications = app_query.count()
    shortlisted = app_query.filter(models.Application.status.in_(["shortlisted", "pitched", "approved", "cast"])).count()
    approved    = app_query.filter(models.Application.status.in_(["approved", "cast"])).count()
    cast        = app_query.filter(models.Application.status == "cast").count()
    rejected    = app_query.filter(models.Application.status == "rejected").count()

    # Applications by status
    status_rows = (
        db.query(models.Application.status, func.count(models.Application.id))
        .filter(models.Application.casting_call_id.in_(scoped_call_ids) if role == "casting_manager" else True)
        .group_by(models.Application.status)
        .all()
    )
    applications_by_status = {row[0]: row[1] for row in status_rows}

    # Recent activity — daily application counts for last 14 days
    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    since = datetime.now(timezone.utc) - timedelta(days=13)
    activity_rows = (
        db.query(
            func.date(models.Application.submitted_at).label("day"),
            func.count(models.Application.id).label("count"),
        )
        .filter(models.Application.submitted_at >= since)
        .filter(models.Application.casting_call_id.in_(scoped_call_ids) if role == "casting_manager" else True)
        .group_by(func.date(models.Application.submitted_at))
        .order_by(func.date(models.Application.submitted_at))
        .all()
    )
    # Fill in zeros for missing days
    activity_map = {str(r.day): r.count for r in activity_rows}
    recent_activity = [
        {"date": str(today - timedelta(days=13 - i)), "count": activity_map.get(str(today - timedelta(days=13 - i)), 0)}
        for i in range(14)
    ]

    # Top casting calls by application count
    top_rows = (
        db.query(
            models.CastingCall.id,
            models.CastingCall.title,
            models.CastingCall.show,
            models.CastingCall.role,
            models.CastingCall.status,
            func.count(models.Application.id).label("application_count"),
        )
        .outerjoin(models.Application, models.Application.casting_call_id == models.CastingCall.id)
        .filter(models.CastingCall.id.in_(scoped_call_ids))
        .group_by(models.CastingCall.id)
        .order_by(func.count(models.Application.id).desc())
        .limit(6)
        .all()
    )
    top_casting_calls = [
        {"id": r.id, "title": r.title, "show": r.show, "role": r.role,
         "status": r.status, "application_count": r.application_count}
        for r in top_rows
    ]

    # Weekly applications for top-3 casting calls (8 weeks, for line chart)
    top3_ids = [r["id"] for r in top_casting_calls[:3]]
    weekly_applications: dict[int, list[int]] = {}
    if top3_ids:
        week_since = datetime.now(timezone.utc) - timedelta(weeks=8)
        for cc_id in top3_ids:
            week_rows = (
                db.query(
                    func.date_trunc("week", models.Application.submitted_at).label("week"),
                    func.count(models.Application.id).label("count"),
                )
                .filter(
                    models.Application.casting_call_id == cc_id,
                    models.Application.submitted_at >= week_since,
                )
                .group_by(func.date_trunc("week", models.Application.submitted_at))
                .order_by(func.date_trunc("week", models.Application.submitted_at))
                .all()
            )
            week_map = {str(r.week.date()): r.count for r in week_rows}
            # Build 8-week array
            weeks_list = []
            for w in range(7, -1, -1):
                wdate = (datetime.now(timezone.utc) - timedelta(weeks=w)).date()
                # normalize to Monday of that week
                monday = wdate - timedelta(days=wdate.weekday())
                weeks_list.append(week_map.get(str(monday), 0))
            weekly_applications[cc_id] = weeks_list

    # Recent applicants (last 5)
    recent_app_rows = (
        db.query(models.Application, models.Applicant, models.CastingCall)
        .join(models.Applicant, models.Application.applicant_id == models.Applicant.id)
        .join(models.CastingCall, models.Application.casting_call_id == models.CastingCall.id)
        .filter(models.Application.casting_call_id.in_(scoped_call_ids) if role == "casting_manager" else True)
        .order_by(models.Application.submitted_at.desc())
        .limit(5)
        .all()
    )
    recent_applicants = [
        {
            "application_id": app.id,
            "name": applicant.name,
            "city": applicant.city,
            "age": applicant.age,
            "status": app.status,
            "role": cc.role,
            "show": cc.show,
            "submitted_at": app.submitted_at.isoformat(),
        }
        for app, applicant, cc in recent_app_rows
    ]

    # Approver queue (last 5 pitch decks, submitted or recently actioned)
    deck_rows = (
        db.query(models.PitchDeck, models.CastingCall)
        .join(models.CastingCall, models.PitchDeck.casting_call_id == models.CastingCall.id)
        .filter(models.CastingCall.id.in_(scoped_call_ids) if role == "casting_manager" else True)
        .order_by(models.PitchDeck.updated_at.desc())
        .limit(5)
        .all()
    )
    approver_queue = [
        {
            "id": deck.id,
            "title": deck.title,
            "show": cc.show,
            "status": deck.status,
            "finalist_count": len(deck.finalists),
        }
        for deck, cc in deck_rows
    ]

    # Activity feed — last 6 audit log entries
    audit_rows = (
        db.query(models.AuditLog)
        .order_by(models.AuditLog.created_at.desc())
        .limit(6)
        .all()
    )
    activity_feed = [
        {
            "id": r.id,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "action": r.action,
            "performed_by": r.performed_by,
            "created_at": r.created_at.isoformat(),
        }
        for r in audit_rows
    ]

    return {
        "total_applications": total_applications,
        "total_casting_calls": total_calls,
        "open_casting_calls": open_calls,
        "draft_casting_calls": draft_calls,
        "shortlisted": shortlisted,
        "approved": approved,
        "cast": cast,
        "rejected": rejected,
        "shortlist_rate": round(shortlisted / total_applications * 100, 1) if total_applications else 0,
        "applications_by_status": applications_by_status,
        "recent_activity": recent_activity,
        "top_casting_calls": top_casting_calls,
        "weekly_applications": weekly_applications,
        "recent_applicants": recent_applicants,
        "approver_queue": approver_queue,
        "activity_feed": activity_feed,
    }
