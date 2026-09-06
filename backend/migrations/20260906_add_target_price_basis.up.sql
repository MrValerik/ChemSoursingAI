ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS target_price_unit VARCHAR(32);

ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS target_price_incoterm VARCHAR(24);
