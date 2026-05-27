"""integer_ids_to_uuid

Revision ID: f1a2b3c4d5e6
Revises: e99ec8fed0f4
Create Date: 2026-05-27 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e99ec8fed0f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. casting_calls ────────────────────────────────────────────────────
    conn.execute(sa.text("ALTER TABLE casting_calls ADD COLUMN new_id UUID DEFAULT gen_random_uuid()"))

    # casting_call_collaborators.casting_call_id → update from parent
    conn.execute(sa.text("ALTER TABLE casting_call_collaborators ADD COLUMN new_casting_call_id UUID"))
    conn.execute(sa.text("""
        UPDATE casting_call_collaborators cc
        SET new_casting_call_id = c.new_id
        FROM casting_calls c
        WHERE c.id = cc.casting_call_id
    """))

    # applications.casting_call_id
    conn.execute(sa.text("ALTER TABLE applications ADD COLUMN new_casting_call_id UUID"))
    conn.execute(sa.text("""
        UPDATE applications a
        SET new_casting_call_id = c.new_id
        FROM casting_calls c
        WHERE c.id = a.casting_call_id
    """))

    # pitch_decks.casting_call_id
    conn.execute(sa.text("ALTER TABLE pitch_decks ADD COLUMN new_casting_call_id UUID"))
    conn.execute(sa.text("""
        UPDATE pitch_decks pd
        SET new_casting_call_id = c.new_id
        FROM casting_calls c
        WHERE c.id = pd.casting_call_id
    """))

    # Drop old FK constraints on child tables referencing casting_calls.id
    conn.execute(sa.text("ALTER TABLE casting_call_collaborators DROP CONSTRAINT casting_call_collaborators_casting_call_id_fkey"))
    conn.execute(sa.text("ALTER TABLE casting_call_collaborators DROP COLUMN casting_call_id"))
    conn.execute(sa.text("ALTER TABLE casting_call_collaborators RENAME COLUMN new_casting_call_id TO casting_call_id"))

    conn.execute(sa.text("ALTER TABLE applications DROP CONSTRAINT applications_casting_call_id_fkey"))

    conn.execute(sa.text("ALTER TABLE pitch_decks DROP CONSTRAINT pitch_decks_casting_call_id_fkey"))

    # Now promote casting_calls.new_id → id
    conn.execute(sa.text("ALTER TABLE casting_calls DROP CONSTRAINT casting_calls_pkey"))
    conn.execute(sa.text("ALTER TABLE casting_calls DROP COLUMN id"))
    conn.execute(sa.text("ALTER TABLE casting_calls RENAME COLUMN new_id TO id"))
    conn.execute(sa.text("ALTER TABLE casting_calls ADD PRIMARY KEY (id)"))
    conn.execute(sa.text("CREATE INDEX ix_casting_calls_id ON casting_calls (id)"))

    # Restore FK from casting_call_collaborators
    conn.execute(sa.text("ALTER TABLE casting_call_collaborators ADD PRIMARY KEY (casting_call_id, user_id)"))
    conn.execute(sa.text("ALTER TABLE casting_call_collaborators ADD CONSTRAINT casting_call_collaborators_casting_call_id_fkey FOREIGN KEY (casting_call_id) REFERENCES casting_calls(id) ON DELETE CASCADE"))

    # ── 2. applicants ───────────────────────────────────────────────────────
    conn.execute(sa.text("ALTER TABLE applicants ADD COLUMN new_id UUID DEFAULT gen_random_uuid()"))

    # applications.applicant_id
    conn.execute(sa.text("ALTER TABLE applications ADD COLUMN new_applicant_id UUID"))
    conn.execute(sa.text("""
        UPDATE applications a
        SET new_applicant_id = ap.new_id
        FROM applicants ap
        WHERE ap.id = a.applicant_id
    """))

    conn.execute(sa.text("ALTER TABLE applications DROP CONSTRAINT applications_applicant_id_fkey"))

    conn.execute(sa.text("ALTER TABLE applicants DROP CONSTRAINT applicants_pkey"))
    conn.execute(sa.text("ALTER TABLE applicants DROP COLUMN id"))
    conn.execute(sa.text("ALTER TABLE applicants RENAME COLUMN new_id TO id"))
    conn.execute(sa.text("ALTER TABLE applicants ADD PRIMARY KEY (id)"))
    conn.execute(sa.text("CREATE INDEX ix_applicants_id ON applicants (id)"))

    # ── 3. applications ─────────────────────────────────────────────────────
    conn.execute(sa.text("ALTER TABLE applications ADD COLUMN new_id UUID DEFAULT gen_random_uuid()"))

    # application_media.application_id
    conn.execute(sa.text("ALTER TABLE application_media ADD COLUMN new_application_id UUID"))
    conn.execute(sa.text("""
        UPDATE application_media am
        SET new_application_id = a.new_id
        FROM applications a
        WHERE a.id = am.application_id
    """))

    # application_tags.application_id
    conn.execute(sa.text("ALTER TABLE application_tags ADD COLUMN new_application_id UUID"))
    conn.execute(sa.text("""
        UPDATE application_tags at2
        SET new_application_id = a.new_id
        FROM applications a
        WHERE a.id = at2.application_id
    """))

    # upload_sessions.application_id
    conn.execute(sa.text("ALTER TABLE upload_sessions ADD COLUMN new_application_id UUID"))
    conn.execute(sa.text("""
        UPDATE upload_sessions us
        SET new_application_id = a.new_id
        FROM applications a
        WHERE a.id = us.application_id
    """))

    # pitch_deck_finalists.application_id
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists ADD COLUMN new_application_id UUID"))
    conn.execute(sa.text("""
        UPDATE pitch_deck_finalists pdf
        SET new_application_id = a.new_id
        FROM applications a
        WHERE a.id = pdf.application_id
    """))

    # Drop old FK constraints on child tables referencing applications.id
    conn.execute(sa.text("ALTER TABLE application_media DROP CONSTRAINT application_media_application_id_fkey"))
    conn.execute(sa.text("ALTER TABLE application_tags DROP CONSTRAINT application_tags_application_id_fkey"))
    conn.execute(sa.text("ALTER TABLE upload_sessions DROP CONSTRAINT upload_sessions_application_id_fkey"))
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists DROP CONSTRAINT pitch_deck_finalists_application_id_fkey"))

    # Promote applications.new_id → id
    conn.execute(sa.text("ALTER TABLE applications DROP CONSTRAINT applications_pkey"))
    conn.execute(sa.text("ALTER TABLE applications DROP COLUMN id"))
    conn.execute(sa.text("ALTER TABLE applications RENAME COLUMN new_id TO id"))
    conn.execute(sa.text("ALTER TABLE applications ADD PRIMARY KEY (id)"))
    conn.execute(sa.text("CREATE INDEX ix_applications_id ON applications (id)"))

    # Fix up applications FK columns now that applications.id is UUID
    conn.execute(sa.text("ALTER TABLE applications DROP COLUMN casting_call_id"))
    conn.execute(sa.text("ALTER TABLE applications RENAME COLUMN new_casting_call_id TO casting_call_id"))
    conn.execute(sa.text("ALTER TABLE applications ALTER COLUMN casting_call_id SET NOT NULL"))
    conn.execute(sa.text("CREATE INDEX ix_applications_casting_call_id ON applications (casting_call_id)"))
    conn.execute(sa.text("ALTER TABLE applications ADD CONSTRAINT applications_casting_call_id_fkey FOREIGN KEY (casting_call_id) REFERENCES casting_calls(id)"))

    conn.execute(sa.text("ALTER TABLE applications DROP COLUMN applicant_id"))
    conn.execute(sa.text("ALTER TABLE applications RENAME COLUMN new_applicant_id TO applicant_id"))
    conn.execute(sa.text("ALTER TABLE applications ALTER COLUMN applicant_id SET NOT NULL"))
    conn.execute(sa.text("CREATE INDEX ix_applications_applicant_id ON applications (applicant_id)"))
    conn.execute(sa.text("ALTER TABLE applications ADD CONSTRAINT applications_applicant_id_fkey FOREIGN KEY (applicant_id) REFERENCES applicants(id)"))

    # pitch_decks FK: drop old column, bring in new
    conn.execute(sa.text("ALTER TABLE pitch_decks DROP COLUMN casting_call_id"))
    conn.execute(sa.text("ALTER TABLE pitch_decks RENAME COLUMN new_casting_call_id TO casting_call_id"))
    conn.execute(sa.text("ALTER TABLE pitch_decks ALTER COLUMN casting_call_id SET NOT NULL"))
    conn.execute(sa.text("CREATE INDEX ix_pitch_decks_casting_call_id ON pitch_decks (casting_call_id)"))
    conn.execute(sa.text("ALTER TABLE pitch_decks ADD CONSTRAINT pitch_decks_casting_call_id_fkey FOREIGN KEY (casting_call_id) REFERENCES casting_calls(id)"))

    # ── 4. application_media ────────────────────────────────────────────────
    conn.execute(sa.text("ALTER TABLE application_media ADD COLUMN new_id UUID DEFAULT gen_random_uuid()"))

    conn.execute(sa.text("ALTER TABLE application_media DROP COLUMN application_id"))
    conn.execute(sa.text("ALTER TABLE application_media RENAME COLUMN new_application_id TO application_id"))
    conn.execute(sa.text("ALTER TABLE application_media ALTER COLUMN application_id SET NOT NULL"))
    conn.execute(sa.text("CREATE INDEX ix_application_media_application_id ON application_media (application_id)"))
    conn.execute(sa.text("ALTER TABLE application_media ADD CONSTRAINT application_media_application_id_fkey FOREIGN KEY (application_id) REFERENCES applications(id)"))

    conn.execute(sa.text("ALTER TABLE application_media DROP CONSTRAINT application_media_pkey"))
    conn.execute(sa.text("ALTER TABLE application_media DROP COLUMN id"))
    conn.execute(sa.text("ALTER TABLE application_media RENAME COLUMN new_id TO id"))
    conn.execute(sa.text("ALTER TABLE application_media ADD PRIMARY KEY (id)"))
    conn.execute(sa.text("CREATE INDEX ix_application_media_id ON application_media (id)"))

    # ── 5. application_tags ─────────────────────────────────────────────────
    conn.execute(sa.text("ALTER TABLE application_tags ADD COLUMN new_id UUID DEFAULT gen_random_uuid()"))

    conn.execute(sa.text("ALTER TABLE application_tags DROP COLUMN application_id"))
    conn.execute(sa.text("ALTER TABLE application_tags RENAME COLUMN new_application_id TO application_id"))
    conn.execute(sa.text("ALTER TABLE application_tags ALTER COLUMN application_id SET NOT NULL"))
    conn.execute(sa.text("CREATE INDEX ix_application_tags_application_id ON application_tags (application_id)"))
    conn.execute(sa.text("ALTER TABLE application_tags ADD CONSTRAINT application_tags_application_id_fkey FOREIGN KEY (application_id) REFERENCES applications(id)"))

    conn.execute(sa.text("ALTER TABLE application_tags DROP CONSTRAINT application_tags_pkey"))
    conn.execute(sa.text("ALTER TABLE application_tags DROP COLUMN id"))
    conn.execute(sa.text("ALTER TABLE application_tags RENAME COLUMN new_id TO id"))
    conn.execute(sa.text("ALTER TABLE application_tags ADD PRIMARY KEY (id)"))
    conn.execute(sa.text("CREATE INDEX ix_application_tags_id ON application_tags (id)"))

    # ── 6. upload_sessions.application_id ──────────────────────────────────
    conn.execute(sa.text("ALTER TABLE upload_sessions DROP COLUMN application_id"))
    conn.execute(sa.text("ALTER TABLE upload_sessions RENAME COLUMN new_application_id TO application_id"))
    conn.execute(sa.text("ALTER TABLE upload_sessions ALTER COLUMN application_id SET NOT NULL"))
    conn.execute(sa.text("ALTER TABLE upload_sessions ADD CONSTRAINT upload_sessions_application_id_fkey FOREIGN KEY (application_id) REFERENCES applications(id)"))

    # ── 7. pitch_decks ──────────────────────────────────────────────────────
    conn.execute(sa.text("ALTER TABLE pitch_decks ADD COLUMN new_id UUID DEFAULT gen_random_uuid()"))

    # pitch_deck_finalists.deck_id
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists ADD COLUMN new_deck_id UUID"))
    conn.execute(sa.text("""
        UPDATE pitch_deck_finalists pdf
        SET new_deck_id = pd.new_id
        FROM pitch_decks pd
        WHERE pd.id = pdf.deck_id
    """))

    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists DROP CONSTRAINT pitch_deck_finalists_deck_id_fkey"))

    conn.execute(sa.text("ALTER TABLE pitch_decks DROP CONSTRAINT pitch_decks_pkey"))
    conn.execute(sa.text("ALTER TABLE pitch_decks DROP COLUMN id"))
    conn.execute(sa.text("ALTER TABLE pitch_decks RENAME COLUMN new_id TO id"))
    conn.execute(sa.text("ALTER TABLE pitch_decks ADD PRIMARY KEY (id)"))
    conn.execute(sa.text("CREATE INDEX ix_pitch_decks_id ON pitch_decks (id)"))

    # ── 8. pitch_deck_finalists ─────────────────────────────────────────────
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists ADD COLUMN new_id UUID DEFAULT gen_random_uuid()"))

    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists DROP COLUMN application_id"))
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists RENAME COLUMN new_application_id TO application_id"))
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists ALTER COLUMN application_id SET NOT NULL"))
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists ADD CONSTRAINT pitch_deck_finalists_application_id_fkey FOREIGN KEY (application_id) REFERENCES applications(id)"))

    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists DROP COLUMN deck_id"))
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists RENAME COLUMN new_deck_id TO deck_id"))
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists ALTER COLUMN deck_id SET NOT NULL"))
    conn.execute(sa.text("CREATE INDEX ix_pitch_deck_finalists_deck_id ON pitch_deck_finalists (deck_id)"))
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists ADD CONSTRAINT pitch_deck_finalists_deck_id_fkey FOREIGN KEY (deck_id) REFERENCES pitch_decks(id) ON DELETE CASCADE"))

    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists DROP CONSTRAINT pitch_deck_finalists_pkey"))
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists DROP COLUMN id"))
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists RENAME COLUMN new_id TO id"))
    conn.execute(sa.text("ALTER TABLE pitch_deck_finalists ADD PRIMARY KEY (id)"))
    conn.execute(sa.text("CREATE INDEX ix_pitch_deck_finalists_id ON pitch_deck_finalists (id)"))

    # ── 9. audit_log ────────────────────────────────────────────────────────
    conn.execute(sa.text("ALTER TABLE audit_log ADD COLUMN new_id UUID DEFAULT gen_random_uuid()"))
    # entity_id: convert integer to text (stores UUID strings going forward)
    conn.execute(sa.text("ALTER TABLE audit_log ALTER COLUMN entity_id TYPE TEXT USING entity_id::TEXT"))

    conn.execute(sa.text("ALTER TABLE audit_log DROP CONSTRAINT audit_log_pkey"))
    conn.execute(sa.text("ALTER TABLE audit_log DROP COLUMN id"))
    conn.execute(sa.text("ALTER TABLE audit_log RENAME COLUMN new_id TO id"))
    conn.execute(sa.text("ALTER TABLE audit_log ADD PRIMARY KEY (id)"))
    conn.execute(sa.text("CREATE INDEX ix_audit_log_id ON audit_log (id)"))

    # ── 10. notifications ───────────────────────────────────────────────────
    conn.execute(sa.text("ALTER TABLE notifications ADD COLUMN new_id UUID DEFAULT gen_random_uuid()"))
    conn.execute(sa.text("ALTER TABLE notifications DROP CONSTRAINT notifications_pkey"))
    conn.execute(sa.text("ALTER TABLE notifications DROP COLUMN id"))
    conn.execute(sa.text("ALTER TABLE notifications RENAME COLUMN new_id TO id"))
    conn.execute(sa.text("ALTER TABLE notifications ADD PRIMARY KEY (id)"))
    conn.execute(sa.text("CREATE INDEX ix_notifications_id ON notifications (id)"))

    # ── 11. in_app_notifications ────────────────────────────────────────────
    conn.execute(sa.text("ALTER TABLE in_app_notifications ADD COLUMN new_id UUID DEFAULT gen_random_uuid()"))
    conn.execute(sa.text("ALTER TABLE in_app_notifications DROP CONSTRAINT in_app_notifications_pkey"))
    conn.execute(sa.text("ALTER TABLE in_app_notifications DROP COLUMN id"))
    conn.execute(sa.text("ALTER TABLE in_app_notifications RENAME COLUMN new_id TO id"))
    conn.execute(sa.text("ALTER TABLE in_app_notifications ADD PRIMARY KEY (id)"))
    conn.execute(sa.text("CREATE INDEX ix_in_app_notifications_id ON in_app_notifications (id)"))

    # ── 12. phone_otp ───────────────────────────────────────────────────────
    conn.execute(sa.text("ALTER TABLE phone_otp ADD COLUMN new_id UUID DEFAULT gen_random_uuid()"))
    conn.execute(sa.text("ALTER TABLE phone_otp DROP CONSTRAINT phone_otp_pkey"))
    conn.execute(sa.text("ALTER TABLE phone_otp DROP COLUMN id"))
    conn.execute(sa.text("ALTER TABLE phone_otp RENAME COLUMN new_id TO id"))
    conn.execute(sa.text("ALTER TABLE phone_otp ADD PRIMARY KEY (id)"))
    conn.execute(sa.text("CREATE INDEX ix_phone_otp_id ON phone_otp (id)"))


def downgrade() -> None:
    raise NotImplementedError("Downgrade from UUID PKs back to integer PKs is not supported.")
