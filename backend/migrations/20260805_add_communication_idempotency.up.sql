ALTER TABLE communications
    ADD COLUMN idempotency_key VARCHAR(36);

CREATE UNIQUE INDEX ix_communications_idempotency_key
    ON communications (idempotency_key);
