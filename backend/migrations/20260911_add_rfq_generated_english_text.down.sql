ALTER TABLE rfqs
    DROP COLUMN IF EXISTS rfq_generated_source_hash,
    DROP COLUMN IF EXISTS rfq_generated_body_en,
    DROP COLUMN IF EXISTS rfq_generated_subject_en;
