ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS deleted_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_rfqs_deleted_at ON rfqs (deleted_at);
CREATE INDEX IF NOT EXISTS ix_rfqs_deleted_by_id ON rfqs (deleted_by_id);
