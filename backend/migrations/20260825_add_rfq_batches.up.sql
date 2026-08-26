-- Пакет закупки: связь между запросами, заведёнными одним списком.
-- Запросы остаются независимыми; пакет хранит только то, что общее у
-- списка: кто завёл, из какого файла и ключ идемпотентности.
CREATE TABLE IF NOT EXISTS rfq_batches (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    idempotency_key VARCHAR(64) NOT NULL,
    source_name VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ключ уникален в пределах закупщика, а не глобально: чужой ключ не должен
-- ни блокировать создание, ни выдавать факт своего существования.
CREATE UNIQUE INDEX IF NOT EXISTS uq_rfq_batches_owner_key
    ON rfq_batches (owner_id, idempotency_key);

CREATE INDEX IF NOT EXISTS ix_rfq_batches_owner_id
    ON rfq_batches (owner_id);

ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS batch_id INTEGER
    REFERENCES rfq_batches(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_rfqs_batch_id ON rfqs (batch_id);
