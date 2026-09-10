DROP TABLE IF EXISTS rfq_analog_candidates;

ALTER TABLE rfqs
    DROP COLUMN IF EXISTS analog_warnings;

ALTER TABLE rfqs
    DROP COLUMN IF EXISTS analog_suggested_at;
