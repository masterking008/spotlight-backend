"""
Supabase Storage helpers for Spotlight Casting Platform.

All media is stored in the 'spotlight-media' bucket.
Access is controlled via signed URLs (bucket has no public access).

Path conventions:
  applications/{tracking_id}/photos/{uuid}_{filename}
  applications/{tracking_id}/videos/{uuid}_{filename}
"""

import os
import re
import time
import uuid
import jwt
from supabase import create_client, Client

BUCKET = "spotlight-media"
SIGNED_URL_EXPIRY = 3600  # 1 hour
VIDEO_MAX_BYTES = 200 * 1024 * 1024  # 200 MB
PHOTO_MAX_BYTES = 5 * 1024 * 1024    # 5 MB


def _admin_client() -> Client:
    """Service-role client — never expose to the frontend."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return create_client(url, key)


def _sanitize_filename(filename: str) -> str:
    """
    Produce a storage-safe filename:
      1. Keep only the extension from the original name (preserves .jpg, .mp4, etc.)
      2. Replace every character that isn't alphanumeric, dash, underscore, or dot
         with an underscore.
      3. Collapse consecutive underscores.
      4. Strip leading/trailing underscores.
    Supabase Storage (and S3-compatible backends) reject keys with spaces,
    parentheses, percent-encoded sequences, and many other shell-special chars.
    """
    if "." in filename:
        name_part, ext = filename.rsplit(".", 1)
        ext = re.sub(r"[^a-zA-Z0-9]", "", ext).lower() or "bin"
    else:
        name_part, ext = filename, "bin"

    safe_name = re.sub(r"[^a-zA-Z0-9\-_]", "_", name_part)
    safe_name = re.sub(r"_+", "_", safe_name).strip("_") or "file"
    return f"{safe_name}.{ext}"


def make_storage_path(tracking_id: str, media_type: str, filename: str) -> str:
    """Collision-safe, storage-key-safe path for a media upload."""
    safe = _sanitize_filename(filename)
    uid = uuid.uuid4().hex[:12]
    folder = "photos" if media_type == "photo" else "videos"
    return f"applications/{tracking_id}/{folder}/{uid}_{safe}"


def create_signed_upload_url(storage_path: str) -> dict:
    """
    Returns a signed URL the client can PUT to directly (photos).
    The client sends the file bytes to this URL — FastAPI never buffers the file.
    """
    client = _admin_client()
    result = client.storage.from_(BUCKET).create_signed_upload_url(storage_path)
    return {
        "signed_url": result.get("signedURL") or result.get("signed_url"),
        "path": result.get("path") or storage_path,
        "token": result.get("token"),
    }


def mint_upload_token(ttl_seconds: int = 300) -> str:
    """
    Mint a short-lived service-role JWT for TUS video uploads.

    Supabase Storage honors the `role` claim in the JWT:
      - role = "service_role"  → bypasses ALL RLS (same as the service key itself)
      - role = "anon"          → subject to RLS policies (causes the 403)

    We sign with SUPABASE_JWT_SECRET (the same secret Supabase uses internally)
    but set `exp` to now + ttl_seconds so the token expires quickly and can't
    be reused if intercepted. Default TTL is 5 minutes — enough for any upload.
    """
    jwt_secret = os.getenv("SUPABASE_JWT_SECRET", "")
    if not jwt_secret:
        raise RuntimeError("SUPABASE_JWT_SECRET must be set in environment")
    now = int(time.time())
    payload = {
        "role": "service_role",
        "iss": "supabase",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, jwt_secret, algorithm="HS256")


def create_signed_read_url(storage_path: str, expiry: int = SIGNED_URL_EXPIRY) -> str:
    """Returns a time-limited read URL for a stored object."""
    client = _admin_client()
    result = client.storage.from_(BUCKET).create_signed_url(storage_path, expiry)
    return result.get("signedURL") or result.get("signed_url") or ""


def delete_object(storage_path: str) -> bool:
    """Delete a single object from storage. Returns True if deleted."""
    try:
        client = _admin_client()
        client.storage.from_(BUCKET).remove([storage_path])
        return True
    except Exception:
        return False


def get_tus_endpoint() -> str:
    """The TUS resumable upload endpoint for this Supabase project."""
    url = os.getenv("SUPABASE_URL", "")
    return f"{url}/storage/v1/upload/resumable"


def get_anon_key() -> str:
    return os.getenv("SUPABASE_ANON_KEY", "")
