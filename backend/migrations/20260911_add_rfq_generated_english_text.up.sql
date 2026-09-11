ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS rfq_generated_subject_en VARCHAR(500),
    ADD COLUMN IF NOT EXISTS rfq_generated_body_en TEXT,
    ADD COLUMN IF NOT EXISTS rfq_generated_source_hash VARCHAR(64);
