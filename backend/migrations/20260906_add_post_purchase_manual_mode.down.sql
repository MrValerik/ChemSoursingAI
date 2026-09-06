ALTER TABLE purchase_decisions
    DROP COLUMN IF EXISTS cancelled_draft_count,
    DROP COLUMN IF EXISTS communication_mode;
