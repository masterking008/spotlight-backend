import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from database import get_db
from rbac import require_role
import models, schemas, crud, otp as otp_svc, storage as storage_svc, notifications as notif_svc

router = APIRouter()


# ─── Applications (Public) ───────────────────────────────────────────────────

@router.post("/applications", response_model=schemas.Application)
async def submit_application(
    body: schemas.ApplicationCreate,
    db: Session = Depends(get_db),
):
    """Public endpoint. Talent submits application — no SSO required."""
    cc = crud.get_casting_call(db, body.casting_call_id)
    if not cc or cc.status != "open":
        raise HTTPException(400, "This casting call is not currently accepting applications")

    if cc.deadline:
        now = datetime.now(timezone.utc)
        dl = cc.deadline if cc.deadline.tzinfo else cc.deadline.replace(tzinfo=timezone.utc)
        if now > dl:
            raise HTTPException(400, "This casting call is no longer accepting applications — the deadline has passed")

    if os.getenv("OTP_SECRET") and body.phone_token:
        try:
            otp_svc.require_phone_token(
                body.phone_token,
                expected_phone=body.applicant.phone,
                expected_purpose="application_submit",
            )
        except ValueError as e:
            raise HTTPException(400, str(e))

    application = crud.create_application(db, body)
    application = crud.get_application(db, application.id)
    if application:
        base_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        status_url = f"{base_url}/status?id={application.tracking_id}"
        try:
            notif_svc.notify_application_received(db, application, status_url)
        except Exception as e:
            print(f"[Notification error] {e}")

    return application


@router.post("/applications/{application_id}/complete", response_model=schemas.Application)
async def complete_application(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """
    Public endpoint — called by the talent form when the applicant clicks
    'Complete Application'. Flips is_complete=True so the application becomes
    visible to casting managers.
    """
    app = db.query(models.Application).filter(models.Application.id == application_id).first()
    if not app:
        raise HTTPException(404, "Application not found")
    if app.is_complete:
        return crud.get_application(db, application_id)  # idempotent
    app.is_complete = True
    db.commit()
    return crud.get_application(db, application_id)


@router.post("/applications/check-status", response_model=schemas.StatusResponse)
async def check_application_status(
    body: schemas.StatusCheck,
    db: Session = Depends(get_db),
):
    """Public status check. Requires phone_token JWT to verify phone ownership."""
    if os.getenv("OTP_SECRET"):
        try:
            payload = otp_svc.decode_phone_token(body.phone_token)
            verified_phone = payload.get("phone")
        except Exception:
            raise HTTPException(401, "Invalid or expired phone verification. Please verify your phone.")
    else:
        verified_phone = None

    application = crud.get_application_by_tracking(db, body.tracking_id)
    if not application:
        raise HTTPException(404, "Application not found. Please check your tracking ID.")

    if verified_phone and application.applicant and application.applicant.phone != verified_phone:
        raise HTTPException(403, "Phone number does not match this application.")

    cc = application.casting_call
    return schemas.StatusResponse(
        status=schemas.ApplicationStatus(application.status),
        application_id=application.id,
        casting_call_title=cc.title if cc else "",
        casting_call_show=cc.show if cc else "",
        casting_call_role=cc.role if cc else "",
        submitted_at=application.submitted_at,
        last_updated=application.updated_at,
    )


# ─── Applications (Manager) ──────────────────────────────────────────────────

@router.get("/casting-calls/{casting_call_id}/applications")
async def get_applications(
    casting_call_id: uuid.UUID,
    response: Response,
    status: Optional[str] = Query(None, description="Comma-separated statuses"),
    tags: Optional[str] = Query(None, description="Comma-separated tag names (AND filter)"),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query("submitted_at_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role("admin", "casting_manager", "approver")),
    db: Session = Depends(get_db),
):
    cc = crud.get_casting_call(db, casting_call_id)
    if not cc:
        raise HTTPException(404, "Casting call not found")

    status_filter = [s.strip() for s in status.split(",")] if status else None
    tag_filter = [t.strip() for t in tags.split(",")] if tags else None
    skip = (page - 1) * page_size

    apps, total = crud.get_applications_by_casting_call(
        db,
        casting_call_id,
        status_filter=status_filter,
        tag_filter=tag_filter,
        search=search,
        sort=sort,
        skip=skip,
        limit=page_size,
    )

    response.headers["X-Total-Count"] = str(total)
    return apps


@router.get("/casting-calls/{casting_call_id}/shortlisted", response_model=List[schemas.Application])
async def get_shortlisted(
    casting_call_id: uuid.UUID,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    return crud.get_shortlisted_applications(db, casting_call_id)


@router.get("/applications", response_model=List[schemas.Application])
async def list_all_applications(
    response: Response,
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Comma-separated statuses"),
    casting_call_id: Optional[uuid.UUID] = Query(None),
    sort: Optional[str] = Query("submitted_at_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role("admin", "casting_manager", "approver")),
    db: Session = Depends(get_db),
):
    status_filter = [s.strip() for s in status.split(",")] if status else None
    skip = (page - 1) * page_size
    apps, total = crud.get_all_applications(
        db,
        search=search,
        status_filter=status_filter,
        casting_call_id=casting_call_id,
        sort=sort,
        skip=skip,
        limit=page_size,
    )
    response.headers["X-Total-Count"] = str(total)
    return apps


@router.get("/applications/search", response_model=List[schemas.Application])
async def search_applications(
    response: Response,
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role("admin", "casting_manager", "approver")),
    db: Session = Depends(get_db),
):
    skip = (page - 1) * page_size
    apps, total = crud.search_applications_global(db, q, skip=skip, limit=page_size)
    response.headers["X-Total-Count"] = str(total)
    return apps


@router.get("/applications/{application_id}", response_model=schemas.Application)
async def get_application(
    application_id: uuid.UUID,
    current_user: dict = Depends(require_role("admin", "casting_manager", "approver")),
    db: Session = Depends(get_db),
):
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(404, "Application not found")
    return application


@router.put("/applications/{application_id}/status")
async def update_status(
    application_id: uuid.UUID,
    body: schemas.StatusUpdate,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(404, "Application not found")

    updated = crud.update_application_status(
        db, application_id, body.status.value, user_id=current_user["id"]
    )

    try:
        notif_svc.notify_status_change(db, updated, body.status.value)
    except Exception as e:
        print(f"[Notification error] {e}")

    return {"message": "Status updated", "status": body.status.value}


@router.patch("/applications/{application_id}/notes")
async def update_notes(
    application_id: uuid.UUID,
    body: schemas.NotesUpdate,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(404, "Application not found")
    crud.update_application_notes(db, application_id, body.notes, current_user["id"])
    return {"message": "Notes updated"}


# ─── Tags ─────────────────────────────────────────────────────────────────────

@router.post("/applications/{application_id}/tags", response_model=schemas.ApplicationTag)
async def add_tag(
    application_id: uuid.UUID,
    body: schemas.TagCreate,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(404, "Application not found")
    return crud.add_application_tag(db, application_id, body, user_id=current_user["id"])


@router.get("/applications/{application_id}/tags", response_model=List[schemas.ApplicationTag])
async def get_tags(
    application_id: uuid.UUID,
    current_user: dict = Depends(require_role("admin", "casting_manager", "approver")),
    db: Session = Depends(get_db),
):
    return crud.get_application_tags(db, application_id)


@router.delete("/applications/{application_id}/tags/{tag_name}")
async def remove_tag(
    application_id: uuid.UUID,
    tag_name: str,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    removed = crud.remove_application_tag(db, application_id, tag_name, current_user["id"])
    if not removed:
        raise HTTPException(404, "Tag not found")
    return {"message": "Tag removed"}


# ─── Media Upload ─────────────────────────────────────────────────────────────

@router.post("/applications/{application_id}/media/photo-upload-url")
async def get_photo_upload_url(
    application_id: uuid.UUID,
    body: schemas.MediaUploadRequest,
    db: Session = Depends(get_db),
):
    """Public: called by the talent application form to get a signed URL for direct photo upload."""
    application = db.query(models.Application).filter(
        models.Application.id == application_id
    ).first()
    if not application:
        raise HTTPException(404, "Application not found")

    if body.file_size > storage_svc.PHOTO_MAX_BYTES:
        raise HTTPException(400, f"Photo must be under 5 MB. Got {body.file_size / 1024 / 1024:.1f} MB")

    storage_path = storage_svc.make_storage_path(
        application.tracking_id, "photo", body.filename
    )
    try:
        result = storage_svc.create_signed_upload_url(storage_path)
    except Exception as e:
        raise HTTPException(500, f"Storage error: {e}")

    session = crud.create_upload_session(
        db,
        application_id=application_id,
        storage_path=storage_path,
        filename=body.filename,
        file_size=body.file_size,
        media_type="photo",
        upload_url=result.get("signed_url"),
    )

    return {
        "session_id": session.id,
        "signed_url": result.get("signed_url"),
        "storage_path": storage_path,
    }


@router.post("/applications/{application_id}/media/video-upload-url")
async def get_video_upload_url(
    application_id: uuid.UUID,
    body: schemas.MediaUploadRequest,
    db: Session = Depends(get_db),
):
    """Public: returns TUS endpoint and credentials for resumable video upload."""
    application = db.query(models.Application).filter(
        models.Application.id == application_id
    ).first()
    if not application:
        raise HTTPException(404, "Application not found")

    if body.file_size > storage_svc.VIDEO_MAX_BYTES:
        raise HTTPException(
            400,
            f"Video must be under 200 MB. Got {body.file_size / 1024 / 1024:.0f} MB"
        )

    storage_path = storage_svc.make_storage_path(
        application.tracking_id, "video", body.filename
    )

    session = crud.create_upload_session(
        db,
        application_id=application_id,
        storage_path=storage_path,
        filename=body.filename,
        file_size=body.file_size,
        media_type="video",
    )

    # Mint a short-lived service-role JWT (5 min TTL) so the public client can
    # POST to the TUS endpoint without triggering bucket RLS.
    upload_token = storage_svc.mint_upload_token(ttl_seconds=300)

    return {
        "session_id": session.id,
        "tus_endpoint": storage_svc.get_tus_endpoint(),
        "upload_token": upload_token,   # pre-authorised JWT — replaces anon key
        "bucket": storage_svc.BUCKET,
        "storage_path": storage_path,
    }


@router.post("/applications/{application_id}/media/complete", response_model=schemas.ApplicationMediaOut)
async def complete_media_upload(
    application_id: uuid.UUID,
    body: schemas.MediaCompleteRequest,
    db: Session = Depends(get_db),
):
    """Called after upload finishes to record the media in the DB."""
    media = crud.complete_upload_session(db, session_id=body.session_id, application_id=application_id)
    if not media:
        raise HTTPException(404, "Upload session not found")
    return media


@router.get("/applications/{application_id}/media", response_model=List[schemas.ApplicationMediaOut])
async def get_media(
    application_id: uuid.UUID,
    current_user: dict = Depends(require_role("admin", "casting_manager", "approver")),
    db: Session = Depends(get_db),
):
    return crud.get_application_media(db, application_id)


@router.delete("/applications/{application_id}/media/{media_id}")
async def delete_media(
    application_id: uuid.UUID,
    media_id: uuid.UUID,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    deleted = crud.delete_media(db, media_id=media_id, application_id=application_id)
    if not deleted:
        raise HTTPException(404, "Media not found")
    return {"message": "Media deleted"}


# ─── Audit Log ────────────────────────────────────────────────────────────────

@router.get("/applications/{application_id}/audit-log", response_model=List[schemas.AuditLogEntry])
async def get_application_audit(
    application_id: uuid.UUID,
    current_user: dict = Depends(require_role("admin", "casting_manager")),
    db: Session = Depends(get_db),
):
    return crud.get_audit_log(db, "application", application_id)
