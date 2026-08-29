DROP INDEX IF EXISTS ix_communications_message_at;

ALTER TABLE communications
    DROP COLUMN IF EXISTS message_at;
