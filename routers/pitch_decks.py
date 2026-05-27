import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from rbac import require_role, require_any_role
import models, schemas, crud, notifications as notif_svc

router = APIRouter()


# ─── Pitch Decks ──────────────────────────────────────────────────────────────

@router.post("/pitch-decks", response_model=schemas.PitchDeckOut)
async def create_pitch_deck(
    body: schemas.PitchDeckCreate,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    return crud.create_pitch_deck(db, body, user_id=current_user["id"])


@router.get("/casting-calls/{casting_call_id}/pitch-decks", response_model=List[schemas.PitchDeckOut])
async def list_pitch_decks(
    casting_call_id: uuid.UUID,
    current_user: dict = Depends(require_any_role()),
    db: Session = Depends(get_db),
):
    return crud.get_pitch_decks_for_call(db, casting_call_id)


@router.get("/pitch-decks/{deck_id}", response_model=schemas.PitchDeckOut)
async def get_pitch_deck(
    deck_id: uuid.UUID,
    current_user: dict = Depends(require_any_role()),
    db: Session = Depends(get_db),
):
    deck = crud.get_pitch_deck(db, deck_id)
    if not deck:
        raise HTTPException(404, "Pitch deck not found")
    for finalist in deck.finalists:
        if finalist.application:
            crud._enrich_application(db, finalist.application)
    return deck


@router.put("/pitch-decks/{deck_id}", response_model=schemas.PitchDeckOut)
async def update_pitch_deck(
    deck_id: uuid.UUID,
    body: schemas.PitchDeckUpdate,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    deck = crud.update_pitch_deck(db, deck_id, body, user_id=current_user["id"])
    if not deck:
        raise HTTPException(404, "Pitch deck not found")
    return deck


@router.post("/pitch-decks/{deck_id}/finalists", response_model=schemas.PitchDeckFinalistOut)
async def add_finalist(
    deck_id: uuid.UUID,
    body: schemas.FinalistCreate,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    deck = db.query(models.PitchDeck).filter(models.PitchDeck.id == deck_id).first()
    if not deck:
        raise HTTPException(404, "Pitch deck not found")
    return crud.add_deck_finalist(db, deck_id, body)


@router.put("/pitch-decks/{deck_id}/finalists/{finalist_id}", response_model=schemas.PitchDeckFinalistOut)
async def update_finalist(
    deck_id: uuid.UUID,
    finalist_id: uuid.UUID,
    body: schemas.FinalistUpdate,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    finalist = crud.update_deck_finalist(db, finalist_id, body)
    if not finalist:
        raise HTTPException(404, "Finalist not found")
    return finalist


@router.delete("/pitch-decks/{deck_id}/finalists/{finalist_id}")
async def remove_finalist(
    deck_id: uuid.UUID,
    finalist_id: uuid.UUID,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    removed = crud.remove_deck_finalist(db, finalist_id=finalist_id, deck_id=deck_id)
    if not removed:
        raise HTTPException(404, "Finalist not found")
    return {"message": "Finalist removed"}


@router.post("/pitch-decks/{deck_id}/submit")
async def submit_pitch_deck(
    deck_id: uuid.UUID,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    deck = crud.get_pitch_deck(db, deck_id)
    if not deck:
        raise HTTPException(404, "Pitch deck not found")
    if len(deck.finalists) < 3:
        raise HTTPException(400, "A pitch deck must have at least 3 finalists before submitting")
    if deck.status not in ("draft", "changes_requested"):
        raise HTTPException(400, f"Deck is already in '{deck.status}' status")

    deck = crud.submit_pitch_deck(db, deck, user_id=current_user["id"])

    approvers = db.query(models.UserProfile).filter(
        models.UserProfile.role.in_(["approver", "admin"]),
        models.UserProfile.is_active == True,
    ).all()
    for approver in approvers:
        if approver.email:
            try:
                notif_svc.notify_deck_submitted(
                    db, deck,
                    approver_user_id=approver.id,
                    approver_email=approver.email,
                )
            except Exception as e:
                print(f"[Notification error] {e}")

    return {"message": "Pitch deck submitted for review", "status": deck.status}


# ─── Approver Actions ─────────────────────────────────────────────────────────

@router.get("/approver/inbox", response_model=List[schemas.PitchDeckOut])
async def approver_inbox(
    current_user: dict = Depends(require_role("admin", "approver")),
    db: Session = Depends(get_db),
):
    return crud.get_submitted_decks_for_approver(db)


@router.post("/pitch-decks/{deck_id}/approve")
async def approve_deck(
    deck_id: uuid.UUID,
    body: schemas.DeckActionRequest,
    current_user: dict = Depends(require_role("admin", "approver")),
    db: Session = Depends(get_db),
):
    deck = crud.get_pitch_deck(db, deck_id)
    if not deck:
        raise HTTPException(404, "Pitch deck not found")
    deck = crud.set_deck_verdict(db, deck, "approved", current_user["id"], body.notes)
    _notify_manager_deck_action(db, deck, "approved", current_user)
    return {"message": "Deck approved", "status": "approved"}


@router.post("/pitch-decks/{deck_id}/reject")
async def reject_deck(
    deck_id: uuid.UUID,
    body: schemas.DeckActionRequest,
    current_user: dict = Depends(require_role("admin", "approver")),
    db: Session = Depends(get_db),
):
    deck = crud.get_pitch_deck(db, deck_id)
    if not deck:
        raise HTTPException(404, "Pitch deck not found")
    deck = crud.set_deck_verdict(db, deck, "rejected", current_user["id"], body.notes)
    _notify_manager_deck_action(db, deck, "rejected", current_user)
    return {"message": "Deck rejected"}


@router.post("/pitch-decks/{deck_id}/request-changes")
async def request_changes(
    deck_id: uuid.UUID,
    body: schemas.DeckActionRequest,
    current_user: dict = Depends(require_role("admin", "approver")),
    db: Session = Depends(get_db),
):
    if not body.notes:
        raise HTTPException(400, "Please provide notes explaining what changes are needed")
    deck = crud.get_pitch_deck(db, deck_id)
    if not deck:
        raise HTTPException(404, "Pitch deck not found")
    deck = crud.set_deck_verdict(db, deck, "changes_requested", current_user["id"], body.notes)
    _notify_manager_deck_action(db, deck, "changes_requested", current_user)
    return {"message": "Changes requested", "status": "changes_requested"}


@router.put("/pitch-decks/{deck_id}/finalists/{finalist_id}/verdict", response_model=schemas.PitchDeckFinalistOut)
async def set_finalist_verdict(
    deck_id: uuid.UUID,
    finalist_id: uuid.UUID,
    body: schemas.FinalistVerdictUpdate,
    current_user: dict = Depends(require_role("admin", "approver")),
    db: Session = Depends(get_db),
):
    finalist = crud.set_finalist_verdict(
        db, finalist_id, body.verdict.value, body.notes or "", current_user["id"]
    )
    if not finalist:
        raise HTTPException(404, "Finalist not found")
    return finalist


# ─── Audit Log ────────────────────────────────────────────────────────────────

@router.get("/pitch-decks/{deck_id}/audit-log", response_model=List[schemas.AuditLogEntry])
async def get_deck_audit(
    deck_id: uuid.UUID,
    current_user: dict = Depends(require_any_role()),
    db: Session = Depends(get_db),
):
    return crud.get_audit_log(db, "pitch_deck", deck_id)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _notify_manager_deck_action(db: Session, deck: models.PitchDeck, action: str, current_user: dict):
    manager_profile = db.query(models.UserProfile).filter(
        models.UserProfile.id == deck.created_by
    ).first()
    if manager_profile and manager_profile.email:
        try:
            notif_svc.notify_deck_action(
                db, deck, action,
                manager_user_id=deck.created_by,
                manager_email=manager_profile.email,
            )
        except Exception as e:
            print(f"[Notification error] {e}")
