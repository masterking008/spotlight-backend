from sqlalchemy.orm import Session
from sqlalchemy import and_
import uuid

import models


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
