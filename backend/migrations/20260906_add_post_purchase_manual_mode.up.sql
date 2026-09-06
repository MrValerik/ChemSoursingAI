ALTER TABLE purchase_decisions
    ADD COLUMN IF NOT EXISTS communication_mode VARCHAR(32)
        NOT NULL DEFAULT 'manual_selected_supplier',
    ADD COLUMN IF NOT EXISTS cancelled_draft_count INTEGER
        NOT NULL DEFAULT 0;
