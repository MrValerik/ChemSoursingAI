CREATE TABLE IF NOT EXISTS substances (
    id SERIAL PRIMARY KEY,
    cas VARCHAR(20) NOT NULL UNIQUE,
    preferred_name VARCHAR(255) NOT NULL,
    synonyms JSONB NOT NULL DEFAULT '[]'::jsonb,
    excluded_names JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes TEXT,
    review_status VARCHAR(32) NOT NULL DEFAULT 'unreviewed',
    verification JSONB,
    reviewed_by_id INTEGER REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_substances_cas ON substances (cas);
CREATE INDEX IF NOT EXISTS ix_substances_review_status ON substances (review_status);
CREATE INDEX IF NOT EXISTS ix_substances_reviewed_by_id ON substances (reviewed_by_id);

ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS substance_id INTEGER REFERENCES substances(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_rfqs_substance_id ON rfqs (substance_id);

INSERT INTO substances (
    cas,
    preferred_name,
    synonyms,
    review_status,
    verification,
    created_at,
    updated_at
)
SELECT
    rfqs.cas,
    min(rfqs.name),
    jsonb_build_array(min(rfqs.name)),
    'unreviewed',
    NULL,
    min(rfqs.created_at),
    max(rfqs.updated_at)
FROM rfqs
WHERE rfqs.cas IS NOT NULL
GROUP BY rfqs.cas
ON CONFLICT (cas) DO NOTHING;

UPDATE rfqs
SET substance_id = substances.id
FROM substances
WHERE substances.cas = rfqs.cas;
