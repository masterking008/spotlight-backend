from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_
import uuid

import models
import storage as storage_svc


def create_upload_session(
    db: Session,
    application_id: uuid.UUID,
    storage_path: str,
    filename: str,
    file_size: int,
    media_type: str,
    upload_url: str = None,
) -> models.UploadSession:
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
