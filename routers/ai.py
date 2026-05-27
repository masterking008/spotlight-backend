import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from rbac import require_any_role
import storage as storage_svc

router = APIRouter()


# ─── Banner upload ────────────────────────────────────────────────────────────

@router.post("/casting-calls/banner-upload")
async def upload_banner(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_any_role()),
):
    """Accept a cropped banner image, store in Supabase, return public URL."""
    import io as _io
    import uuid as _uuid

    allowed = {"image/jpeg", "image/png", "image/webp"}
    ct = file.content_type or ""
    if ct not in allowed:
        raise HTTPException(status_code=422, detail="Only JPEG, PNG or WebP images are allowed")

    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Banner must be under 8 MB")

    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(ct, "jpg")
    path = f"banners/{_uuid.uuid4().hex}.{ext}"

    try:
        client = storage_svc._admin_client()
        client.storage.from_(storage_svc.BUCKET).upload(
            path, raw, {"content-type": ct, "upsert": "true"}
        )
        signed = storage_svc.create_signed_read_url(path, expiry=60 * 60 * 24 * 365 * 5)
        return {"url": signed, "path": path}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Storage error: {e}")


# ─── AI brief → casting call ──────────────────────────────────────────────────

@router.post("/ai/fill-casting-call")
async def ai_fill_casting_call(
    brief: str = Form(default=""),
    file: Optional[UploadFile] = File(default=None),
    current_user: dict = Depends(require_any_role()),
):
    import openai as _openai
    import json as _json
    import io as _io

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OpenAI API key not configured")

    # ── Extract text ──────────────────────────────────────────────────────────
    file_text = ""
    if file and file.filename:
        raw = await file.read()
        fname = (file.filename or "").lower()
        if fname.endswith(".pdf"):
            try:
                from pypdf import PdfReader
                reader = PdfReader(_io.BytesIO(raw))
                file_text = "\n".join(p.extract_text() or "" for p in reader.pages)
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"Could not read PDF: {e}")
        elif fname.endswith(".docx"):
            try:
                import docx as _docx
                doc = _docx.Document(_io.BytesIO(raw))
                file_text = "\n".join(p.text for p in doc.paragraphs)
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"Could not read DOCX: {e}")
        else:
            raise HTTPException(status_code=422, detail="Only PDF or DOCX files are supported")

    combined = "\n\n".join(filter(None, [brief.strip(), file_text.strip()]))
    if not combined:
        raise HTTPException(status_code=422, detail="Provide a brief text or upload a file")

    # ── Call OpenAI ───────────────────────────────────────────────────────────
    from datetime import datetime, timedelta, timezone
    today_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    SYSTEM = f"""You are an expert casting director assistant for Indian TV and film productions.
Today's date is {today_str}.

Given a casting brief, extract all details and generate a COMPLETE structured casting call including a FULL application questionnaire.

Return ONLY a valid JSON object with EXACTLY this shape — no markdown fences, no extra keys, no explanation:

{{
  "title": "concise title, e.g. Male Lead – Zindagi Ke Rang S3",
  "show": "exact show/project name from the brief",
  "role": "role name and type, e.g. Arjun Mehta – Male Lead",
  "description": "3-5 sentence role description covering character, tone, physical/language/acting requirements, and shoot details",
  "deadline": "YYYY-MM-DDTHH:MM — extract the exact deadline from the brief; if only a date is mentioned use T23:59; if no deadline found default to 30 days from today",
  "status": "open",
  "form_fields": [
    {{
      "type": "one of: text | number | textarea | select | multiselect | checkbox | file",
      "label": "natural conversational question label",
      "placeholder": "helpful example or hint (optional)",
      "required": true,
      "options": ["Option A", "Option B"],
      "mediaType": "photo or video — ONLY for type=file fields",
      "maxFiles": 5,
      "maxSizeMB": 10
    }}
  ]
}}

CRITICAL RULES FOR form_fields — you MUST generate 6 to 10 fields:
1. Read EVERY requirement in the brief and turn each into a field. Do not skip any.
2. Physical requirements (height, build, look) → number or text fields
3. Acting experience years → number field
4. Training/institute → select field with common Indian acting schools as options
5. Prior TV/film credits → textarea field
6. Languages/accents mentioned → multiselect field
7. Availability/relocation → checkbox or select field
8. Any file/media explicitly requested (headshots, self-tape, monologue) → file fields with correct mediaType
9. Any portfolio/IMDB link → text field
10. The standard fields name, phone, age, city, languages are ALREADY collected — do NOT duplicate them
11. options array is ONLY for select/multiselect — completely omit it for all other types
12. For file fields you MUST include mediaType ("photo" or "video"), maxFiles, and maxSizeMB
13. Make labels sound like a human casting director asking the question, not a form label"""

    client = _openai.OpenAI(api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"CASTING BRIEF:\n\n{combined}"},
            ],
            temperature=0.3,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenAI error: {e}")

    try:
        data = _json.loads(resp.choices[0].message.content or "{}")
    except Exception:
        raise HTTPException(status_code=502, detail="AI returned invalid JSON")

    # ── Parse deadline ────────────────────────────────────────────────────────
    raw_deadline = data.get("deadline", "")
    deadline_iso = ""
    if raw_deadline:
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                deadline_iso = datetime.strptime(raw_deadline[:16], fmt[:len(raw_deadline[:16])]).strftime("%Y-%m-%dT%H:%M")
                break
            except Exception:
                continue
    if not deadline_iso:
        deadline_iso = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M")

    # ── Build form_schema ─────────────────────────────────────────────────────
    import uuid as _uuid
    fields = []
    for f in data.get("form_fields", []):
        ftype = f.get("type", "text")
        if ftype not in ("text","number","email","tel","textarea","select","multiselect","date","file","checkbox"):
            ftype = "text"
        field: dict = {
            "id": str(_uuid.uuid4()),
            "type": ftype,
            "label": f.get("label", "Field"),
            "required": bool(f.get("required", False)),
        }
        if f.get("placeholder"):
            field["placeholder"] = f["placeholder"]
        if ftype in ("select", "multiselect") and f.get("options"):
            field["options"] = f["options"]
        if ftype == "file":
            field["mediaType"] = f.get("mediaType", "photo")
            field["maxFiles"] = int(f.get("maxFiles", 5))
            field["maxSizeMB"] = int(f.get("maxSizeMB", 5 if f.get("mediaType") != "video" else 200))
        if ftype == "number" and (f.get("min") is not None or f.get("max") is not None):
            field["validation"] = {}
            if f.get("min") is not None: field["validation"]["min"] = f["min"]
            if f.get("max") is not None: field["validation"]["max"] = f["max"]
        fields.append(field)

    return {
        "title": data.get("title", ""),
        "show": data.get("show", ""),
        "role": data.get("role", ""),
        "description": data.get("description", ""),
        "deadline": deadline_iso,
        "status": data.get("status", "open"),
        "form_schema": {
            "version": 1,
            "fields": fields,
            "settings": {
                "requireConsent": True,
                "consentText": "I consent to Rusk Media using my submission for casting purposes.",
            },
        },
    }
