DROP INDEX IF EXISTS ix_communications_idempotency_key;

ALTER TABLE communications DROP COLUMN IF EXISTS idempotency_key;
