ALTER TABLE rfqs
    ADD COLUMN deleted_at TIMESTAMPTZ;

ALTER TABLE rfqs
    ADD COLUMN deleted_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX ix_rfqs_deleted_at ON rfqs (deleted_at);
CREATE INDEX ix_rfqs_deleted_by_id ON rfqs (deleted_by_id);
