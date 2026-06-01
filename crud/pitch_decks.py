from datetime import datetime, timezone
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_
import uuid

import models
import schemas
from .audit import log_action
from .applications import _enrich_application


def _sync_deck_finalists(db: Session, deck: models.PitchDeck, finalists_data: list) -> None:
    db.query(models.PitchDeckFinalist).filter(
        models.PitchDeckFinalist.deck_id == deck.id
    ).delete(synchronize_session=False)
    for f in finalists_data:
        db.add(models.PitchDeckFinalist(
            deck_id=deck.id,
            application_id=f.application_id,
            position=f.position,
            manager_notes=f.manager_notes,
            approver_verdict="pending",
        ))


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
            joinedload(models.PitchDeck.reviewer_notes_list),
        )
        .filter(models.PitchDeck.id == deck_id)
        .first()
    )


def create_pitch_deck(db: Session, data: schemas.PitchDeckCreate, user_id: str) -> models.PitchDeck:
    deck = models.PitchDeck(
        casting_call_id=data.casting_call_id,
        created_by=user_id,
        title=data.title,
        notes=data.notes,
    )
    db.add(deck)
    db.flush()
    if data.finalists:
        _sync_deck_finalists(db, deck, data.finalists)
    db.commit()
    db.refresh(deck)
    log_action(db, entity_type="pitch_deck", entity_id=deck.id,
               action="created", performed_by=user_id, new_value={"title": deck.title})
    return get_pitch_deck(db, deck.id)


def get_all_pitch_decks(db: Session, user_id: str, role: str) -> list[models.PitchDeck]:
    query = db.query(models.PitchDeck).options(joinedload(models.PitchDeck.casting_call))
    if role not in ("admin", "approver"):
        collab_subq = (
            db.query(models.casting_call_collaborators.c.casting_call_id)
            .filter(models.casting_call_collaborators.c.user_id == user_id)
            .subquery()
        )
        query = query.join(models.CastingCall).filter(
            or_(
                models.CastingCall.owner_id == user_id,
                models.CastingCall.id.in_(collab_subq),
            )
        )
    else:
        query = query.join(models.CastingCall)
    return query.order_by(models.PitchDeck.created_at.desc()).all()


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
    for field, value in data.model_dump(exclude_unset=True, exclude={"finalists"}).items():
        setattr(deck, field, value)
    deck.updated_at = datetime.now(timezone.utc)
    if data.finalists is not None:
        _sync_deck_finalists(db, deck, data.finalists)
    db.commit()
    return get_pitch_deck(db, deck_id)


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


def add_reviewer_note(db: Session, deck_id: uuid.UUID, reviewer_id: str, note: str) -> models.PitchDeckNote:
    obj = models.PitchDeckNote(deck_id=deck_id, reviewer_id=reviewer_id, note=note)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    profile = db.query(models.UserProfile.full_name, models.UserProfile.email).filter(
        models.UserProfile.id == reviewer_id
    ).first()
    if profile:
        obj.reviewer_name = profile.full_name or profile.email
    log_action(db, entity_type="pitch_deck", entity_id=deck_id,
               action="note_added", performed_by=reviewer_id, new_value={"note": note})
    return obj


def get_reviewer_notes(db: Session, deck_id: uuid.UUID) -> list[models.PitchDeckNote]:
    notes = (
        db.query(models.PitchDeckNote)
        .filter(models.PitchDeckNote.deck_id == deck_id)
        .order_by(models.PitchDeckNote.created_at.asc())
        .all()
    )
    reviewer_ids = {n.reviewer_id for n in notes}
    if reviewer_ids:
        profiles = db.query(
            models.UserProfile.id, models.UserProfile.full_name, models.UserProfile.email
        ).filter(models.UserProfile.id.in_(reviewer_ids)).all()
        name_map = {p.id: (p.full_name or p.email) for p in profiles}
        for n in notes:
            n.reviewer_name = name_map.get(n.reviewer_id)
    return notes


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
               action=action, performed_by=reviewer_id, new_value={"notes": notes})
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
