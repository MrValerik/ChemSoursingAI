ALTER TABLE communications
    ADD COLUMN message_at TIMESTAMPTZ;

UPDATE communications
SET message_at = created_at
WHERE message_at IS NULL;

CREATE INDEX ix_communications_message_at
    ON communications (message_at);
