from pydantic import BaseModel, field_validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
from uuid import UUID


# ─── UUID coercion helper ─────────────────────────────────────────────────────
# SQLAlchemy PGUUID(as_uuid=True) returns Python UUID objects, but our schema
# fields are typed str.  This annotated alias runs str() before type-checking.
from typing import Annotated
from pydantic import BeforeValidator

UUIDStr = Annotated[str, BeforeValidator(str)]


# ─── Enums ────────────────────────────────────────────────────────────────────

class ApplicationStatus(str, Enum):
    NEW = "new"
    REVIEWING = "reviewing"
    SHORTLISTED = "shortlisted"
    PITCHED = "pitched"
    APPROVED = "approved"
    CAST = "cast"
    REJECTED = "rejected"


class CastingCallStatus(str, Enum):
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"


class AppRole(str, Enum):
    ADMIN = "admin"
    CASTING_MANAGER = "casting_manager"
    APPROVER = "approver"
    VIEWER = "viewer"


class DeckStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class FinalistVerdict(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING = "pending"


# ─── Casting Calls ─────────────────────────────────────────────────────────────

class CastingCallBase(BaseModel):
    title: str
    show: str
    role: str
    description: Optional[str] = None
    deadline: Optional[datetime] = None
    form_schema: Optional[Dict[str, Any]] = None
    banner_url: Optional[str] = None


class CastingCallCreate(CastingCallBase):
    # owner_id set server-side from JWT; accepted here so frontend can send it
    # but overridden in the endpoint
    owner_id: Optional[str] = None


class CastingCallUpdate(BaseModel):
    title: Optional[str] = None
    show: Optional[str] = None
    role: Optional[str] = None
    description: Optional[str] = None
    deadline: Optional[datetime] = None
    status: Optional[CastingCallStatus] = None
    form_schema: Optional[Dict[str, Any]] = None
    banner_url: Optional[str] = None


class CollaboratorOut(BaseModel):
    id: UUIDStr
    email: str
    full_name: Optional[str] = None

    model_config = {"from_attributes": True}


class CastingCall(CastingCallBase):
    id: UUIDStr
    status: CastingCallStatus
    owner_id: str
    collaborators: List[CollaboratorOut] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CollaboratorAdd(BaseModel):
    user_id: UUIDStr


# ─── Applicants ────────────────────────────────────────────────────────────────

class ApplicantBase(BaseModel):
    phone: str
    name: str
    email: Optional[str] = None
    age: Optional[int] = None
    city: Optional[str] = None
    languages: Optional[List[str]] = None


class ApplicantCreate(ApplicantBase):
    profile_data: Optional[Dict[str, Any]] = None


class Applicant(ApplicantBase):
    id: UUIDStr
    profile_data: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Applications ──────────────────────────────────────────────────────────────

class ApplicationCreate(BaseModel):
    casting_call_id: UUIDStr
    applicant: ApplicantCreate
    custom_responses: Optional[Dict[str, Any]] = None
    consent_given: bool = False
    phone_token: Optional[str] = None  # JWT from OTP verification (required in prod)


class StatusUpdate(BaseModel):
    """Wraps the status enum so FastAPI can parse it from JSON body correctly."""
    status: ApplicationStatus


class NotesUpdate(BaseModel):
    notes: str


class Application(BaseModel):
    id: UUIDStr
    casting_call_id: UUIDStr
    applicant_id: UUIDStr
    status: ApplicationStatus
    tracking_id: str
    consent_given: bool
    is_complete: bool = False
    notes: Optional[str] = None
    submitted_at: datetime
    updated_at: datetime
    custom_responses: Optional[Dict[str, Any]] = None
    applicant: Optional[Applicant] = None
    tags: Optional[List["ApplicationTag"]] = None
    media: Optional[List["ApplicationMediaOut"]] = None

    model_config = {"from_attributes": True}


# ─── Media ─────────────────────────────────────────────────────────────────────

class MediaUploadRequest(BaseModel):
    filename: str
    file_size: int
    media_type: str  # photo | video


class MediaCompleteRequest(BaseModel):
    session_id: str
    storage_path: str
    media_type: str


class ApplicationMediaOut(BaseModel):
    id: UUIDStr
    application_id: UUIDStr
    type: str
    url: Optional[str] = None        # freshly signed read URL
    storage_path: Optional[str] = None
    filename: Optional[str] = None
    file_size: Optional[int] = None
    duration: Optional[int] = None
    thumbnail_url: Optional[str] = None
    upload_status: str
    uploaded_at: datetime

    model_config = {"from_attributes": True}


# ─── Tags ──────────────────────────────────────────────────────────────────────

class TagCreate(BaseModel):
    tag_name: str
    applied_by: Optional[str] = None  # set server-side from JWT


class ApplicationTag(BaseModel):
    id: UUIDStr
    application_id: UUIDStr
    tag_name: str
    applied_by: str
    applied_at: datetime

    model_config = {"from_attributes": True}


# ─── Status Tracker ────────────────────────────────────────────────────────────

class StatusCheck(BaseModel):
    phone_token: str   # JWT from OTP verification
    tracking_id: str


class StatusResponse(BaseModel):
    status: ApplicationStatus
    application_id: UUIDStr
    casting_call_title: str
    casting_call_show: str
    casting_call_role: str
    submitted_at: datetime
    last_updated: datetime


# ─── User Profiles ────────────────────────────────────────────────────────────

class UserProfileOut(BaseModel):
    """Full profile as returned by admin list and /me/profile endpoints."""
    id: UUIDStr                          # Supabase UUID
    email: str
    full_name: Optional[str] = None
    role: AppRole
    team: Optional[str] = None
    sub_team: Optional[str] = None
    designation: Optional[str] = None
    location: Optional[str] = None
    is_active: bool = True
    employee_id: Optional[str] = None
    mobile: Optional[str] = None
    manager_id: Optional[str] = None
    manager_email: Optional[str] = None
    manager_name: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserProfileUpdate(BaseModel):
    """Fields a user or admin may update on a profile."""
    full_name: Optional[str] = None
    team: Optional[str] = None
    sub_team: Optional[str] = None
    designation: Optional[str] = None
    location: Optional[str] = None
    mobile: Optional[str] = None
    employee_id: Optional[str] = None
    manager_id: Optional[str] = None
    manager_email: Optional[str] = None
    manager_name: Optional[str] = None
    is_active: Optional[bool] = None


class UserRoleAssign(BaseModel):
    role: AppRole


# Backwards-compat alias
UserRoleOut = UserProfileOut


# ─── OTP ──────────────────────────────────────────────────────────────────────

class OTPRequest(BaseModel):
    phone: str
    purpose: str  # application_submit | status_check

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, v: str) -> str:
        digits = "".join(c for c in v if c.isdigit() or c == "+")
        if len(digits) < 10:
            raise ValueError("Phone number too short")
        return digits

    @field_validator("purpose")
    @classmethod
    def valid_purpose(cls, v: str) -> str:
        allowed = {"application_submit", "status_check"}
        if v not in allowed:
            raise ValueError(f"purpose must be one of {allowed}")
        return v


class OTPVerify(BaseModel):
    phone: str
    otp: str
    purpose: str


class OTPVerifyResponse(BaseModel):
    phone_token: str


# ─── Pitch Decks ──────────────────────────────────────────────────────────────

class PitchDeckCreate(BaseModel):
    casting_call_id: UUIDStr
    title: str
    notes: Optional[str] = None


class PitchDeckUpdate(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None


class FinalistCreate(BaseModel):
    application_id: UUIDStr
    position: int
    manager_notes: Optional[str] = None


class FinalistUpdate(BaseModel):
    position: Optional[int] = None
    manager_notes: Optional[str] = None


class FinalistVerdictUpdate(BaseModel):
    verdict: FinalistVerdict
    notes: Optional[str] = None


class PitchDeckFinalistOut(BaseModel):
    id: UUIDStr
    deck_id: UUIDStr
    application_id: UUIDStr
    position: int
    manager_notes: Optional[str] = None
    approver_verdict: Optional[str] = None
    approver_notes: Optional[str] = None
    application: Optional[Application] = None

    model_config = {"from_attributes": True}


class PitchDeckOut(BaseModel):
    id: UUIDStr
    casting_call_id: UUIDStr
    created_by: str
    title: str
    notes: Optional[str] = None
    status: DeckStatus
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    reviewer_id: Optional[str] = None
    reviewer_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    finalists: List[PitchDeckFinalistOut] = []

    model_config = {"from_attributes": True}


class DeckActionRequest(BaseModel):
    notes: Optional[str] = None


# ─── Audit Log ────────────────────────────────────────────────────────────────

class AuditLogEntry(BaseModel):
    id: UUIDStr
    entity_type: str
    entity_id: UUIDStr
    action: str
    performed_by: str
    previous_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── In-App Notifications ─────────────────────────────────────────────────────

class InAppNotificationOut(BaseModel):
    id: UUIDStr
    user_id: UUIDStr
    title: str
    body: Optional[str] = None
    link: Optional[str] = None
    read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# Resolve forward references
Application.model_rebuild()
PitchDeckFinalistOut.model_rebuild()
PitchDeckOut.model_rebuild()
