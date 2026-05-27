"""
Role-based access control for Spotlight Casting Platform.

Roles (highest to lowest):
  admin           — full access, user management
  casting_manager — create/manage casting calls, review applications
  approver        — view assigned pitch decks, take approval actions
  viewer          — read-only on assigned shows

Roles are stored in user_profiles.role (not in Supabase JWT).
"""

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from auth import get_current_user
import models


ROLE_HIERARCHY = {
    "admin": 4,
    "casting_manager": 3,
    "approver": 2,
    "viewer": 1,
}


def get_user_profile(db: Session, user_id: str) -> models.UserProfile | None:
    return db.query(models.UserProfile).filter(
        models.UserProfile.id == user_id
    ).first()


# Backwards-compat alias
def get_user_role(db: Session, user_id: str) -> models.UserProfile | None:
    return get_user_profile(db, user_id)


def require_role(*allowed_roles: str):
    """
    FastAPI dependency factory.
    Usage: current_user=Depends(require_role("admin", "casting_manager"))
    Returns the user dict with "app_role" injected.
    """
    async def dependency(
        current_user: dict = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        profile = get_user_profile(db, current_user["id"])
        if not profile or not profile.is_active:
            raise HTTPException(
                status_code=403,
                detail="Account not found or deactivated. Contact an admin."
            )
        if profile.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{profile.role}' is not permitted for this action. "
                       f"Required: {list(allowed_roles)}"
            )
        current_user["app_role"] = profile.role
        current_user["profile"] = profile
        return current_user

    return dependency


def require_any_role():
    """Requires user to have any valid role (just be in the system and active)."""
    return require_role("admin", "casting_manager", "approver", "viewer")


def can_manage_casting_call(user: dict, casting_call: models.CastingCall) -> bool:
    """True if the user can edit this specific casting call."""
    if user.get("app_role") == "admin":
        return True
    if user.get("app_role") == "casting_manager":
        if casting_call.owner_id == user["id"]:
            return True
        # Co-managers (collaborators) have the same edit rights as the owner
        collaborator_ids = [c.id for c in (casting_call.collaborators or [])]
        if user["id"] in collaborator_ids:
            return True
    return False


def assert_can_manage(user: dict, casting_call: models.CastingCall):
    if not can_manage_casting_call(user, casting_call):
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to manage this casting call."
        )
