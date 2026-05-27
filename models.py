import re
import uuid

from sqlalchemy import (
    Column, Integer, String, DateTime, Text, ForeignKey,
    JSON, Boolean, BigInteger, Table
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import enum


class CastingRole(str, enum.Enum):
    admin = "admin"
    casting_manager = "casting_manager"
    approver = "approver"
    viewer = "viewer"

# Backwards compat alias
UserRoleEnum = CastingRole


# ─── Association table: casting call collaborators ────────────────────────────

casting_call_collaborators = Table(
    "casting_call_collaborators",
    Base.metadata,
    Column("casting_call_id", PGUUID(as_uuid=True), ForeignKey("casting_calls.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", String, ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True),
)


class CastingCall(Base):
    __tablename__ = "casting_calls"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    title = Column(String, nullable=False)
    show = Column(String, nullable=False)
    role = Column(String, nullable=False)
    show_slug = Column(String, index=True)
    role_slug = Column(String, index=True)
    description = Column(Text)
    deadline = Column(DateTime(timezone=True))
    status = Column(String, default="draft")
    owner_id = Column(String, nullable=False, index=True)
    form_schema = Column(JSON)
    banner_url = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    applications = relationship("Application", back_populates="casting_call")
    pitch_decks = relationship("PitchDeck", back_populates="casting_call")
    collaborators = relationship("UserProfile", secondary="casting_call_collaborators", lazy="selectin")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.show:
            self.show_slug = self.create_slug(self.show)
        if self.role:
            self.role_slug = self.create_slug(self.role)

    @staticmethod
    def create_slug(text: str) -> str:
        """Convert text to URL-friendly slug"""
        if not text:
            return ""
        # Remove special characters, convert to lowercase, replace spaces with hyphens
        slug = re.sub(r'[^\w\s-]', '', text.lower().strip())
        slug = re.sub(r'[\s_-]+', '-', slug)
        slug = re.sub(r'^-+|-+$', '', slug)  # Remove leading/trailing hyphens
        return slug

    def update_slugs(self):
        """Update slugs when show or role changes"""
        if self.show:
            self.show_slug = self.create_slug(self.show)
        if self.role:
            self.role_slug = self.create_slug(self.role)


class Applicant(Base):
    __tablename__ = "applicants"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    phone = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    email = Column(String)
    age = Column(Integer)
    city = Column(String)
    languages = Column(JSON)
    profile_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    applications = relationship("Application", back_populates="applicant")


class Application(Base):
    __tablename__ = "applications"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    casting_call_id = Column(PGUUID(as_uuid=True), ForeignKey("casting_calls.id"), nullable=False, index=True)
    applicant_id = Column(PGUUID(as_uuid=True), ForeignKey("applicants.id"), nullable=False, index=True)
    status = Column(String, default="new", index=True)
    custom_responses = Column(JSON)
    tracking_id = Column(String, unique=True, index=True)
    consent_given = Column(Boolean, default=False, nullable=False)
    is_complete = Column(Boolean, default=False, nullable=False, index=True)
    notes = Column(Text)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    casting_call = relationship("CastingCall", back_populates="applications")
    applicant = relationship("Applicant", back_populates="applications")
    media = relationship("ApplicationMedia", back_populates="application", cascade="all, delete-orphan")
    tags = relationship("ApplicationTag", back_populates="application", cascade="all, delete-orphan")


class ApplicationMedia(Base):
    __tablename__ = "application_media"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    application_id = Column(PGUUID(as_uuid=True), ForeignKey("applications.id"), nullable=False, index=True)
    type = Column(String, nullable=False)          # photo | video
    url = Column(String)                           # signed read URL (regenerated per request)
    storage_path = Column(String)                  # permanent path in Supabase Storage
    filename = Column(String)
    file_size = Column(BigInteger)
    duration = Column(Integer)                     # seconds, for video
    thumbnail_url = Column(String)
    upload_status = Column(String, default="complete")  # pending | complete | failed
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())

    application = relationship("Application", back_populates="media")


class ApplicationTag(Base):
    __tablename__ = "application_tags"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    application_id = Column(PGUUID(as_uuid=True), ForeignKey("applications.id"), nullable=False, index=True)
    tag_name = Column(String, nullable=False)
    applied_by = Column(String, nullable=False)
    applied_at = Column(DateTime(timezone=True), server_default=func.now())

    application = relationship("Application", back_populates="tags")


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    id = Column(String, primary_key=True)          # UUID hex string
    application_id = Column(PGUUID(as_uuid=True), ForeignKey("applications.id"), nullable=False)
    filename = Column(String, nullable=False)
    file_size = Column(BigInteger)
    media_type = Column(String, nullable=False)    # photo | video
    storage_path = Column(String, nullable=False)
    upload_url = Column(Text)
    expires_at = Column(DateTime(timezone=True))
    completed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserProfile(Base):
    """
    One row per Rusk Media employee who has signed in via Azure AD SSO.
    Auto-provisioned on first login; role defaults to 'viewer' until an admin promotes.
    """
    __tablename__ = "user_profiles"

    id = Column(String, primary_key=True)          # Supabase auth.users UUID (sub claim)
    email = Column(String, nullable=False, unique=True, index=True)
    full_name = Column(String)
    role = Column(String, nullable=False, default="viewer")
    team = Column(String)
    sub_team = Column(String)
    designation = Column(String)
    location = Column(String)
    org_id = Column(String)                        # future: FK to orgs table
    is_active = Column(Boolean, default=True)
    employee_id = Column(String)
    mobile = Column(String)
    manager_id = Column(String, ForeignKey("user_profiles.id", ondelete="SET NULL"))
    manager_email = Column(String)
    manager_name = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # self-referential manager relationship
    manager = relationship("UserProfile", remote_side="UserProfile.id", foreign_keys=[manager_id])

# Backwards compat alias — code that imports UserRole still works
UserRole = UserProfile


class PitchDeck(Base):
    __tablename__ = "pitch_decks"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    casting_call_id = Column(PGUUID(as_uuid=True), ForeignKey("casting_calls.id"), nullable=False, index=True)
    created_by = Column(String, nullable=False)    # user_id
    title = Column(String, nullable=False)
    notes = Column(Text)
    status = Column(String, default="draft", index=True)  # draft | submitted | approved | rejected | changes_requested
    submitted_at = Column(DateTime(timezone=True))
    reviewed_at = Column(DateTime(timezone=True))
    reviewer_id = Column(String)
    reviewer_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    casting_call = relationship("CastingCall", back_populates="pitch_decks")
    finalists = relationship("PitchDeckFinalist", back_populates="deck", cascade="all, delete-orphan", order_by="PitchDeckFinalist.position")


class PitchDeckFinalist(Base):
    __tablename__ = "pitch_deck_finalists"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    deck_id = Column(PGUUID(as_uuid=True), ForeignKey("pitch_decks.id", ondelete="CASCADE"), nullable=False, index=True)
    application_id = Column(PGUUID(as_uuid=True), ForeignKey("applications.id"), nullable=False)
    position = Column(Integer, nullable=False)
    manager_notes = Column(Text)
    approver_verdict = Column(String)              # approved | rejected | pending
    approver_notes = Column(Text)

    deck = relationship("PitchDeck", back_populates="finalists")
    application = relationship("Application")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    entity_type = Column(String, nullable=False, index=True)   # application | pitch_deck | casting_call
    entity_id = Column(Text, nullable=False, index=True)        # UUID stored as text
    action = Column(String, nullable=False)
    performed_by = Column(String, nullable=False)              # user_id or "system"
    previous_value = Column(JSON)
    new_value = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    recipient_type = Column(String, nullable=False)   # talent | manager | approver
    recipient_id = Column(String)                     # user_id for manager/approver
    recipient_phone = Column(String)
    recipient_email = Column(String)
    channel = Column(String, nullable=False)          # whatsapp | email | in_app
    template_key = Column(String, nullable=False)
    payload = Column(JSON)
    status = Column(String, default="pending")        # pending | sent | failed
    sent_at = Column(DateTime(timezone=True))
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InAppNotification(Base):
    __tablename__ = "in_app_notifications"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    body = Column(Text)
    link = Column(String)
    read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PhoneOTP(Base):
    __tablename__ = "phone_otp"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    phone = Column(String, nullable=False, index=True)
    otp_hash = Column(String, nullable=False)
    purpose = Column(String, nullable=False)          # application_submit | status_check
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
