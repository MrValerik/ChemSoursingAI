CREATE TABLE substance_revisions (
    id SERIAL PRIMARY KEY,
    substance_id INTEGER NOT NULL REFERENCES substances(id) ON DELETE CASCADE,
    action VARCHAR(40) NOT NULL,
    changes JSONB NOT NULL DEFAULT '{}'::jsonb,
    snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    source_rfq_id INTEGER REFERENCES rfqs(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_substance_revisions_substance_id
    ON substance_revisions (substance_id);
CREATE INDEX ix_substance_revisions_action
    ON substance_revisions (action);
CREATE INDEX ix_substance_revisions_actor_id
    ON substance_revisions (actor_id);
CREATE INDEX ix_substance_revisions_source_rfq_id
    ON substance_revisions (source_rfq_id);
