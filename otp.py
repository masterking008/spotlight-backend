"""
Phone OTP verification for Spotlight Casting Platform.

Flow:
  1. POST /otp/send   → generate 6-digit OTP, store bcrypt hash, send SMS via Twilio
  2. POST /otp/verify → verify OTP, issue short-lived signed phone_token JWT
  3. Application submit / status check require this phone_token

The phone_token is a HS256 JWT signed with OTP_SECRET (separate from Supabase JWT secret).
"""

import os
import random
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_

import models


OTP_TOKEN_EXPIRY_MINUTES = 120  # phone_token valid for 2 hours after verification
OTP_EXPIRY_MINUTES = 10
OTP_RATE_LIMIT = 3              # max OTPs per phone per 10 min window


class OTPError(Exception):
    pass


def _get_secret() -> str:
    secret = os.getenv("OTP_SECRET")
    if not secret:
        raise RuntimeError("OTP_SECRET env var is not set")
    return secret


def _send_sms(phone: str, otp: str):
    """Send OTP via Twilio SMS. Falls back to stdout in dev mode."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_FROM_NUMBER")

    if not all([account_sid, auth_token, from_number]):
        # Dev mode — just print the OTP
        print(f"[DEV] OTP for {phone}: {otp}")
        return

    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        client.messages.create(
            body=f"Your Spotlight verification code is: {otp}. Valid for {OTP_EXPIRY_MINUTES} minutes.",
            from_=from_number,
            to=phone,
        )
    except Exception as e:
        print(f"[SMS ERROR] Failed to send OTP to {phone}: {e}")
        raise OTPError("Failed to send SMS. Please try again.")


def check_rate_limit(db: Session, phone: str) -> None:
    """Raise OTPError if too many OTPs have been requested recently."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    count = db.query(models.PhoneOTP).filter(
        and_(
            models.PhoneOTP.phone == phone,
            models.PhoneOTP.created_at >= cutoff,
        )
    ).count()
    if count >= OTP_RATE_LIMIT:
        raise OTPError("Too many OTP requests. Please wait 10 minutes before trying again.")


def send_otp(db: Session, phone: str, purpose: str) -> None:
    """Generate OTP, store hash, send SMS."""
    check_rate_limit(db, phone)

    otp = str(random.randint(100000, 999999))
    hashed = bcrypt.hashpw(otp.encode(), bcrypt.gensalt()).decode()

    db_obj = models.PhoneOTP(
        phone=phone,
        otp_hash=hashed,
        purpose=purpose,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES),
    )
    db.add(db_obj)
    db.commit()

    _send_sms(phone, otp)


def verify_otp_and_issue_token(db: Session, phone: str, otp: str, purpose: str) -> str:
    """
    Verify the OTP. On success, mark it used and return a signed phone_token JWT.
    Raises OTPError on any failure — never reveal whether phone exists.
    """
    now = datetime.now(timezone.utc)
    record = (
        db.query(models.PhoneOTP)
        .filter(
            and_(
                models.PhoneOTP.phone == phone,
                models.PhoneOTP.purpose == purpose,
                models.PhoneOTP.used == False,
                models.PhoneOTP.expires_at > now,
            )
        )
        .order_by(models.PhoneOTP.created_at.desc())
        .first()
    )

    if not record:
        raise OTPError("OTP not found or expired. Please request a new one.")

    if not bcrypt.checkpw(otp.encode(), record.otp_hash.encode()):
        raise OTPError("Incorrect OTP. Please check and try again.")

    # Mark used
    record.used = True
    db.commit()

    # Issue phone_token
    payload = {
        "phone": phone,
        "purpose": purpose,
        "exp": now + timedelta(minutes=OTP_TOKEN_EXPIRY_MINUTES),
        "iat": now,
    }
    token = jwt.encode(payload, _get_secret(), algorithm="HS256")
    return token


def decode_phone_token(token: str) -> dict:
    """
    Decode and validate a phone_token JWT.
    Returns the payload dict. Raises jwt.ExpiredSignatureError or jwt.InvalidTokenError on failure.
    """
    return jwt.decode(token, _get_secret(), algorithms=["HS256"])


def require_phone_token(phone_token: str, expected_phone: str, expected_purpose: str) -> None:
    """
    Validate a phone_token matches the expected phone and purpose.
    Raises HTTPException-friendly ValueError on failure.
    """
    try:
        payload = decode_phone_token(phone_token)
    except jwt.ExpiredSignatureError:
        raise ValueError("Phone verification has expired. Please verify your phone again.")
    except Exception:
        raise ValueError("Invalid phone verification token.")

    if payload.get("phone") != expected_phone:
        raise ValueError("Phone number does not match verification token.")
    if payload.get("purpose") != expected_purpose:
        raise ValueError("Invalid token purpose.")
