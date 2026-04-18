-- idempotency fields for release_intake submission summary email
ALTER TABLE submissions
  ADD COLUMN IF NOT EXISTS summary_email_sent boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS summary_email_sent_at timestamptz,
  ADD COLUMN IF NOT EXISTS summary_email_message_id text;
