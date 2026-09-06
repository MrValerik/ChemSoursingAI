CREATE TABLE IF NOT EXISTS communication_rfq_links (
    communication_id INTEGER NOT NULL
        REFERENCES communications(id) ON DELETE CASCADE,
    rfq_id INTEGER NOT NULL
        REFERENCES rfqs(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_communication_rfq_links
        PRIMARY KEY (communication_id, rfq_id)
);

CREATE INDEX IF NOT EXISTS ix_communication_rfq_links_rfq_id
    ON communication_rfq_links (rfq_id);

INSERT INTO communication_rfq_links (communication_id, rfq_id)
SELECT id, rfq_id
FROM communications
WHERE rfq_id IS NOT NULL
ON CONFLICT (communication_id, rfq_id) DO NOTHING;
