ALTER TABLE feedback_messages
    DROP COLUMN IF EXISTS email_delivery_attempted_at,
    DROP COLUMN IF EXISTS email_message_id,
    DROP COLUMN IF EXISTS email_delivery_status;
