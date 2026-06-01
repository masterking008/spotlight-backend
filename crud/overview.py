from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

import models


def get_overview(db: Session, user_id: str, role: str) -> dict:
    cc_query = db.query(models.CastingCall)
    if role == "casting_manager":
        collab_subq = (
            db.query(models.casting_call_collaborators.c.casting_call_id)
            .filter(models.casting_call_collaborators.c.user_id == user_id)
            .subquery()
        )
        cc_query = cc_query.filter(
            or_(
                models.CastingCall.owner_id == user_id,
                models.CastingCall.id.in_(collab_subq),
            )
        )

    total_calls = cc_query.count()
    open_calls  = cc_query.filter(models.CastingCall.status == "open").count()
    draft_calls = cc_query.filter(models.CastingCall.status == "draft").count()

    scoped_call_ids = [r.id for r in cc_query.with_entities(models.CastingCall.id).all()]

    app_query = db.query(models.Application)
    if role == "casting_manager":
        app_query = app_query.filter(models.Application.casting_call_id.in_(scoped_call_ids))

    total_applications = app_query.count()
    shortlisted = app_query.filter(models.Application.status.in_(["shortlisted", "pitched", "approved", "cast"])).count()
    approved    = app_query.filter(models.Application.status.in_(["approved", "cast"])).count()
    cast        = app_query.filter(models.Application.status == "cast").count()
    rejected    = app_query.filter(models.Application.status == "rejected").count()

    status_rows = (
        db.query(models.Application.status, func.count(models.Application.id))
        .filter(models.Application.casting_call_id.in_(scoped_call_ids) if role == "casting_manager" else True)
        .group_by(models.Application.status)
        .all()
    )
    applications_by_status = {row[0]: row[1] for row in status_rows}

    today = datetime.now(timezone.utc).date()
    since = datetime.now(timezone.utc) - timedelta(days=13)
    activity_rows = (
        db.query(
            func.date(models.Application.submitted_at).label("day"),
            func.count(models.Application.id).label("count"),
        )
        .filter(models.Application.submitted_at >= since)
        .filter(models.Application.casting_call_id.in_(scoped_call_ids) if role == "casting_manager" else True)
        .group_by(func.date(models.Application.submitted_at))
        .order_by(func.date(models.Application.submitted_at))
        .all()
    )
    activity_map = {str(r.day): r.count for r in activity_rows}
    recent_activity = [
        {"date": str(today - timedelta(days=13 - i)), "count": activity_map.get(str(today - timedelta(days=13 - i)), 0)}
        for i in range(14)
    ]

    top_rows = (
        db.query(
            models.CastingCall.id,
            models.CastingCall.title,
            models.CastingCall.show,
            models.CastingCall.role,
            models.CastingCall.status,
            func.count(models.Application.id).label("application_count"),
        )
        .outerjoin(models.Application, models.Application.casting_call_id == models.CastingCall.id)
        .filter(models.CastingCall.id.in_(scoped_call_ids))
        .group_by(models.CastingCall.id)
        .order_by(func.count(models.Application.id).desc())
        .limit(6)
        .all()
    )
    top_casting_calls = [
        {"id": r.id, "title": r.title, "show": r.show, "role": r.role,
         "status": r.status, "application_count": r.application_count}
        for r in top_rows
    ]

    top3_ids = [r["id"] for r in top_casting_calls[:3]]
    weekly_applications: dict[int, list[int]] = {}
    if top3_ids:
        week_since = datetime.now(timezone.utc) - timedelta(weeks=8)
        for cc_id in top3_ids:
            week_rows = (
                db.query(
                    func.date_trunc("week", models.Application.submitted_at).label("week"),
                    func.count(models.Application.id).label("count"),
                )
                .filter(
                    models.Application.casting_call_id == cc_id,
                    models.Application.submitted_at >= week_since,
                )
                .group_by(func.date_trunc("week", models.Application.submitted_at))
                .order_by(func.date_trunc("week", models.Application.submitted_at))
                .all()
            )
            week_map = {str(r.week.date()): r.count for r in week_rows}
            weeks_list = []
            for w in range(7, -1, -1):
                wdate = (datetime.now(timezone.utc) - timedelta(weeks=w)).date()
                monday = wdate - timedelta(days=wdate.weekday())
                weeks_list.append(week_map.get(str(monday), 0))
            weekly_applications[cc_id] = weeks_list

    recent_app_rows = (
        db.query(models.Application, models.Applicant, models.CastingCall)
        .join(models.Applicant, models.Application.applicant_id == models.Applicant.id)
        .join(models.CastingCall, models.Application.casting_call_id == models.CastingCall.id)
        .filter(models.Application.casting_call_id.in_(scoped_call_ids) if role == "casting_manager" else True)
        .order_by(models.Application.submitted_at.desc())
        .limit(5)
        .all()
    )
    recent_applicants = [
        {
            "application_id": app.id,
            "name": applicant.name,
            "city": applicant.city,
            "age": applicant.age,
            "status": app.status,
            "role": cc.role,
            "show": cc.show,
            "submitted_at": app.submitted_at.isoformat(),
        }
        for app, applicant, cc in recent_app_rows
    ]

    deck_rows = (
        db.query(models.PitchDeck, models.CastingCall)
        .join(models.CastingCall, models.PitchDeck.casting_call_id == models.CastingCall.id)
        .filter(models.CastingCall.id.in_(scoped_call_ids) if role == "casting_manager" else True)
        .order_by(models.PitchDeck.updated_at.desc())
        .limit(5)
        .all()
    )
    approver_queue = [
        {
            "id": deck.id,
            "title": deck.title,
            "show": cc.show,
            "status": deck.status,
            "finalist_count": len(deck.finalists),
        }
        for deck, cc in deck_rows
    ]

    audit_rows = (
        db.query(models.AuditLog)
        .order_by(models.AuditLog.created_at.desc())
        .limit(6)
        .all()
    )
    activity_feed = [
        {
            "id": r.id,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "action": r.action,
            "performed_by": r.performed_by,
            "created_at": r.created_at.isoformat(),
        }
        for r in audit_rows
    ]

    return {
        "total_applications": total_applications,
        "total_casting_calls": total_calls,
        "open_casting_calls": open_calls,
        "draft_casting_calls": draft_calls,
        "shortlisted": shortlisted,
        "approved": approved,
        "cast": cast,
        "rejected": rejected,
        "shortlist_rate": round(shortlisted / total_applications * 100, 1) if total_applications else 0,
        "applications_by_status": applications_by_status,
        "recent_activity": recent_activity,
        "top_casting_calls": top_casting_calls,
        "weekly_applications": weekly_applications,
        "recent_applicants": recent_applicants,
        "approver_queue": approver_queue,
        "activity_feed": activity_feed,
    }
