ALTER TABLE feedback_messages
    ADD COLUMN IF NOT EXISTS email_delivery_status VARCHAR(32)
        NOT NULL DEFAULT 'not_attempted',
    ADD COLUMN IF NOT EXISTS email_message_id VARCHAR(998),
    ADD COLUMN IF NOT EXISTS email_delivery_attempted_at TIMESTAMPTZ;
