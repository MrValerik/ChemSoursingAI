-- Двухступенчатый подбор аналогов: сначала вещества-кандидаты с
-- доказательствами, потом выбор закупщика, и только потом поиск компаний.

ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS analog_suggested_at TIMESTAMPTZ;

ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS analog_warnings JSONB;

CREATE TABLE IF NOT EXISTS rfq_analog_candidates (
    id SERIAL PRIMARY KEY,
    rfq_id INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    cas VARCHAR(20),
    cas_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    reason TEXT NOT NULL DEFAULT '',
    quote TEXT,
    source_url VARCHAR(500),
    selected BOOLEAN NOT NULL DEFAULT FALSE,
    -- Удаление заведённого запроса не уносит подбор: он объясняет, почему
    -- этот запрос вообще появился.
    created_rfq_id INTEGER REFERENCES rfqs(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_rfq_analog_candidates_rfq_id
    ON rfq_analog_candidates (rfq_id);

CREATE INDEX IF NOT EXISTS ix_rfq_analog_candidates_selected
    ON rfq_analog_candidates (selected);

CREATE INDEX IF NOT EXISTS ix_rfq_analog_candidates_created_rfq_id
    ON rfq_analog_candidates (created_rfq_id);
