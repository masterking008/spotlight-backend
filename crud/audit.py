from datetime import datetime, date
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_
import uuid
import decimal

import models


def _json_safe(obj):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if hasattr(obj, 'value'):
        return obj.value
    if isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


def log_action(
    db: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    performed_by: str,
    previous_value: dict = None,
    new_value: dict = None,
):
    entry = models.AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        performed_by=performed_by,
        previous_value=_json_safe(previous_value),
        new_value=_json_safe(new_value),
    )
    db.add(entry)
    db.commit()
    return entry


def get_audit_log(
    db: Session, entity_type: str, entity_id, limit: int = 50
) -> list[models.AuditLog]:
    entries = (
        db.query(models.AuditLog)
        .filter(
            and_(
                models.AuditLog.entity_type == entity_type,
                models.AuditLog.entity_id == str(entity_id),
            )
        )
        .order_by(models.AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    user_ids = {e.performed_by for e in entries if e.performed_by and e.performed_by != "system"}
    if user_ids:
        profiles = db.query(
            models.UserProfile.id, models.UserProfile.full_name, models.UserProfile.email
        ).filter(models.UserProfile.id.in_(user_ids)).all()
        name_map = {p.id: (p.full_name or p.email) for p in profiles}
        for entry in entries:
            entry.performed_by_name = name_map.get(entry.performed_by, entry.performed_by)
    return entries
