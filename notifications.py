"""
Notification service for Spotlight Casting Platform.

Channels:
  - WhatsApp: Meta Cloud API (production) / Twilio WhatsApp sandbox (dev)
  - Email: SendGrid
  - In-app: Stored in in_app_notifications table, served via API + Supabase Realtime

All sends are fire-and-forget from within the request cycle.
Heavy volume should move to a task queue (n8n / Celery) in production.
"""

import os
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
import models


# ─── WhatsApp Templates ───────────────────────────────────────────────────────
# Register these with Meta Business Manager before going live.
WHATSAPP_TEMPLATES = {
    "application_received": (
        "Hi {name}! Your application for *{role}* on *{show}* has been received. "
        "Your tracking ID is *{tracking_id}*. Save this to check your status at {status_url}"
    ),
    "status_shortlisted": (
        "Great news, {name}! You've been shortlisted for *{role}* on *{show}*. "
        "Our team will be in touch with next steps."
    ),
    "status_approved": (
        "Congratulations {name}! You've been approved for *{role}* on *{show}*. "
        "Our casting team will contact you soon with further details."
    ),
    "status_cast": (
        "Welcome to the team, {name}! You've been cast for *{role}* on *{show}*. "
        "Our production team will reach out with schedule details."
    ),
    "status_rejected": (
        "Hi {name}, thank you for applying to *{role}* on *{show}*. "
        "We've moved forward with other candidates. We'll keep your profile for future roles."
    ),
}

STATUS_TO_TEMPLATE = {
    "shortlisted": "status_shortlisted",
    "approved": "status_approved",
    "cast": "status_cast",
    "rejected": "status_rejected",
}


def _send_whatsapp(phone: str, template_key: str, params: dict) -> bool:
    """Send via Twilio WhatsApp (dev) or Meta Cloud API (prod)."""
    message_body = WHATSAPP_TEMPLATES.get(template_key, "")
    try:
        message_body = message_body.format(**params)
    except KeyError:
        pass

    meta_token = os.getenv("META_WHATSAPP_TOKEN")
    meta_phone_id = os.getenv("META_WHATSAPP_PHONE_ID")

    if meta_token and meta_phone_id:
        # Production: Meta Cloud API
        try:
            import httpx
            resp = httpx.post(
                f"https://graph.facebook.com/v19.0/{meta_phone_id}/messages",
                headers={"Authorization": f"Bearer {meta_token}", "Content-Type": "application/json"},
                json={
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "text",
                    "text": {"body": message_body},
                },
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as e:
            print(f"[WhatsApp META ERROR] {e}")
            return False

    # Dev: Twilio WhatsApp sandbox
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

    if account_sid and auth_token:
        try:
            from twilio.rest import Client
            client = Client(account_sid, auth_token)
            client.messages.create(
                body=message_body,
                from_=from_number,
                to=f"whatsapp:{phone}",
            )
            return True
        except Exception as e:
            print(f"[WhatsApp TWILIO ERROR] {e}")
            return False

    # No credentials — log to console
    print(f"[DEV WhatsApp] → {phone} | {template_key}: {message_body}")
    return True


def _send_email(to_email: str, subject: str, body_html: str) -> bool:
    """Send via SendGrid."""
    api_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "noreply@ruskmedia.com")

    if not api_key:
        print(f"[DEV Email] → {to_email} | {subject}")
        return True

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        sg = SendGridAPIClient(api_key)
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            html_content=body_html,
        )
        sg.send(message)
        return True
    except Exception as e:
        print(f"[Email ERROR] {e}")
        return False


def create_in_app(
    db: Session,
    user_id: str,
    title: str,
    body: str,
    link: str = None,
) -> models.InAppNotification:
    """Create an in-app notification record (picked up via Supabase Realtime on frontend)."""
    notif = models.InAppNotification(
        user_id=user_id,
        title=title,
        body=body,
        link=link,
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)
    return notif


def _record_notification(
    db: Session,
    *,
    recipient_type: str,
    channel: str,
    template_key: str,
    payload: dict,
    recipient_id: str = None,
    recipient_phone: str = None,
    recipient_email: str = None,
    success: bool = True,
):
    record = models.Notification(
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        recipient_phone=recipient_phone,
        recipient_email=recipient_email,
        channel=channel,
        template_key=template_key,
        payload=payload,
        status="sent" if success else "failed",
        sent_at=datetime.now(timezone.utc) if success else None,
    )
    db.add(record)
    db.commit()


# ─── High-Level Trigger Functions ─────────────────────────────────────────────

def notify_application_received(db: Session, application: models.Application, status_url: str):
    applicant = application.applicant
    if not applicant:
        return
    params = {
        "name": applicant.name,
        "role": application.casting_call.role if application.casting_call else "role",
        "show": application.casting_call.show if application.casting_call else "show",
        "tracking_id": application.tracking_id,
        "status_url": status_url,
    }
    ok = _send_whatsapp(applicant.phone, "application_received", params)
    _record_notification(
        db, recipient_type="talent", channel="whatsapp",
        template_key="application_received", payload=params,
        recipient_phone=applicant.phone, success=ok,
    )


def notify_status_change(db: Session, application: models.Application, new_status: str):
    template_key = STATUS_TO_TEMPLATE.get(new_status)
    if not template_key:
        return  # only notify for specific statuses
    applicant = application.applicant
    if not applicant:
        return
    params = {
        "name": applicant.name,
        "role": application.casting_call.role if application.casting_call else "role",
        "show": application.casting_call.show if application.casting_call else "show",
    }
    ok = _send_whatsapp(applicant.phone, template_key, params)
    _record_notification(
        db, recipient_type="talent", channel="whatsapp",
        template_key=template_key, payload=params,
        recipient_phone=applicant.phone, success=ok,
    )


def notify_deck_submitted(db: Session, deck: models.PitchDeck, approver_user_id: str, approver_email: str):
    cc = deck.casting_call
    subject = f"[Spotlight] Pitch deck ready for review: {cc.title if cc else 'Casting Call'}"
    body = f"""
    <p>A pitch deck has been submitted for your review.</p>
    <p><strong>Casting Call:</strong> {cc.title if cc else ''}<br>
    <strong>Show:</strong> {cc.show if cc else ''}<br>
    <strong>Deck:</strong> {deck.title}</p>
    <p><a href="{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/pitch-decks/{deck.id}">Review Deck →</a></p>
    """
    ok = _send_email(approver_email, subject, body)
    _record_notification(
        db, recipient_type="approver", channel="email",
        template_key="deck_submitted", payload={"deck_id": deck.id},
        recipient_id=approver_user_id, recipient_email=approver_email, success=ok,
    )
    create_in_app(
        db, user_id=approver_user_id,
        title=f"Pitch deck awaiting review: {deck.title}",
        body=f"{cc.show if cc else ''} — {len(deck.finalists)} finalists",
        link=f"/pitch-decks/{deck.id}",
    )


def notify_deck_action(db: Session, deck: models.PitchDeck, action: str, manager_user_id: str, manager_email: str):
    titles = {
        "approved": "✅ Pitch deck approved",
        "rejected": "❌ Pitch deck rejected",
        "changes_requested": "💬 Changes requested on pitch deck",
    }
    title = titles.get(action, f"Pitch deck update: {action}")
    subject = f"[Spotlight] {title}: {deck.title}"
    body = f"""
    <p>{title}</p>
    <p><strong>Deck:</strong> {deck.title}</p>
    {"<p><strong>Notes:</strong> " + deck.reviewer_notes + "</p>" if deck.reviewer_notes else ""}
    <p><a href="{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/pitch-decks/{deck.id}">View Deck →</a></p>
    """
    ok = _send_email(manager_email, subject, body)
    _record_notification(
        db, recipient_type="manager", channel="email",
        template_key=f"deck_{action}", payload={"deck_id": deck.id},
        recipient_id=manager_user_id, recipient_email=manager_email, success=ok,
    )
    create_in_app(
        db, user_id=manager_user_id,
        title=title,
        body=deck.reviewer_notes or deck.title,
        link=f"/pitch-decks/{deck.id}",
    )
