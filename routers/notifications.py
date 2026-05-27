import uuid
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from auth import get_current_user
import schemas, crud

router = APIRouter()


@router.get("/notifications", response_model=List[schemas.InAppNotificationOut])
async def get_notifications(
    skip: int = 0,
    limit: int = 30,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return crud.get_in_app_notifications(db, current_user["id"], skip=skip, limit=limit)


@router.get("/notifications/unread-count")
async def get_unread_count(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return {"count": crud.get_unread_count(db, current_user["id"])}


@router.put("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    crud.mark_notification_read(db, notification_id, current_user["id"])
    return {"message": "Marked as read"}


@router.put("/notifications/read-all")
async def mark_all_read(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    crud.mark_all_read(db, current_user["id"])
    return {"message": "All notifications marked as read"}
