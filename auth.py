"""
auth.py — JWT validation, domain enforcement, and profile auto-provisioning.

Rules:
  1. Only @ruskmedia.com emails may access the internal dashboard.
  2. On every authenticated request we ensure a user_profiles row exists.
     - First login → create with role='viewer'
     - Subsequent logins → update full_name if it changed in Azure AD
  3. Public endpoints (talent application, status check) bypass domain check.
"""

import os
import jwt
from jwt import PyJWKClient
from typing import Optional
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

ALLOWED_DOMAIN = "ruskmedia.com"

security = HTTPBearer()

_jwks_client: PyJWKClient | None = None


def get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        supabase_url = os.getenv("SUPABASE_URL")
        _jwks_client = PyJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json")
    return _jwks_client


def _decode_jwt(token: str) -> dict:
    """Decode and verify a Supabase JWT. Returns the payload."""
    try:
        jwks_client = get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256", "HS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except Exception as e:
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
            print(f"JWT error: {e} | header={header} | payload={unverified}")
        except Exception:
            pass
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def _enforce_domain(email: str | None) -> None:
    """Raise 403 if email is not from the allowed domain."""
    if not email or not email.lower().endswith(f"@{ALLOWED_DOMAIN}"):
        raise HTTPException(
            status_code=403,
            detail=f"Access restricted to @{ALLOWED_DOMAIN} accounts. "
                   f"Sign in with your Rusk Media email.",
        )


def _ensure_profile(payload: dict, db: Session) -> None:
    """
    Auto-provision or update a user_profiles row on every login.
    Import here (not top-level) to avoid circular imports with main.py.
    """
    # Lazy import to avoid circular dep
    from models import UserProfile

    user_id: str = payload["sub"]
    email: str = payload.get("email", "")
    full_name: str = (
        payload.get("user_metadata", {}).get("full_name")
        or payload.get("user_metadata", {}).get("name")
        or ""
    )

    profile = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if profile is None:
        # First login — provision with viewer role
        profile = UserProfile(
            id=user_id,
            email=email,
            full_name=full_name or None,
            role="viewer",
            is_active=True,
        )
        db.add(profile)
        db.commit()
        print(f"[auth] New user provisioned: {email} (id={user_id})")
    elif full_name and profile.full_name != full_name:
        # Sync display name from Azure AD on subsequent logins
        profile.full_name = full_name
        db.commit()


# ─── FastAPI dependencies ─────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Validate JWT and return user dict.
    Does NOT enforce domain or provision profile — use get_internal_user for that.
    """
    payload = _decode_jwt(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing sub")

    return {
        "id": user_id,
        "email": payload.get("email"),
        "role": payload.get("role", "authenticated"),
        "metadata": payload.get("user_metadata", {}),
    }


def make_internal_user_dep(db_dep):
    """
    Factory that returns a FastAPI dependency which:
      1. Validates JWT
      2. Enforces @ruskmedia.com domain
      3. Auto-provisions/updates user_profiles row
      4. Returns user dict

    Usage in main.py:
        get_internal_user = auth.make_internal_user_dep(get_db)
        @app.get("/foo")
        def foo(user=Depends(get_internal_user)): ...
    """
    async def _dep(
        credentials: HTTPAuthorizationCredentials = Depends(security),
        db: Session = Depends(db_dep),
    ) -> dict:
        payload = _decode_jwt(credentials.credentials)
        email = payload.get("email", "")
        _enforce_domain(email)
        _ensure_profile(payload, db)

        return {
            "id": payload["sub"],
            "email": email,
            "role": payload.get("role", "authenticated"),
            "metadata": payload.get("user_metadata", {}),
        }

    return _dep


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(
        HTTPBearer(auto_error=False)
    ),
):
    """For public endpoints that optionally accept auth (talent apply, status check)."""
    if not credentials:
        return None
    return await get_current_user(credentials)
