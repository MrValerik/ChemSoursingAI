ALTER TABLE quotations
    ADD COLUMN IF NOT EXISTS field_provenance JSONB;

CREATE TABLE IF NOT EXISTS quotation_field_audits (
    id BIGSERIAL PRIMARY KEY,
    quotation_id INTEGER NOT NULL REFERENCES quotations(id) ON DELETE CASCADE,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    field_name VARCHAR(64) NOT NULL,
    old_value JSONB,
    new_value JSONB,
    old_source VARCHAR(32),
    new_source VARCHAR(32) NOT NULL DEFAULT 'human',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_quotation_field_audits_quotation_id
    ON quotation_field_audits (quotation_id);
CREATE INDEX IF NOT EXISTS ix_quotation_field_audits_actor_id
    ON quotation_field_audits (actor_id);
CREATE INDEX IF NOT EXISTS ix_quotation_field_audits_field_name
    ON quotation_field_audits (field_name);
