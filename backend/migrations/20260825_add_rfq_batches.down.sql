DROP INDEX IF EXISTS ix_rfqs_batch_id;

ALTER TABLE rfqs
    DROP COLUMN IF EXISTS batch_id;

DROP INDEX IF EXISTS ix_rfq_batches_owner_id;
DROP INDEX IF EXISTS uq_rfq_batches_owner_key;

DROP TABLE IF EXISTS rfq_batches;
