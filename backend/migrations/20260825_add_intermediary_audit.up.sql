-- Кто, почему и по какому результату отметил домен посредником.
-- Правило отсева меняет будущие поиски всех закупщиков, поэтому оно должно
-- быть предъявимым: без автора и причины запись неотличима от стартового
-- списка и её нельзя оспорить.
ALTER TABLE intermediaries
    ADD COLUMN IF NOT EXISTS added_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS reason TEXT,
    ADD COLUMN IF NOT EXISTS source_url VARCHAR(1000),
    ADD COLUMN IF NOT EXISTS source_rfq_id INTEGER REFERENCES rfqs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS deactivated_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_intermediaries_added_by_id
    ON intermediaries (added_by_id);
CREATE INDEX IF NOT EXISTS ix_intermediaries_source_rfq_id
    ON intermediaries (source_rfq_id);
