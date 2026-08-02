-- Реестр посредников: торговых площадок, каталогов и перекупщиков.
-- Запрос по CAS-номеру поднимает в выдаче площадки, а не заводы, и они
-- съедают бюджет загрузки страниц. Список ведётся как данные, потому что
-- пополняет его закупщик, а не разработчик.

CREATE TABLE IF NOT EXISTS intermediaries (
    id SERIAL PRIMARY KEY,
    domain VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    kind VARCHAR(32) NOT NULL DEFAULT 'marketplace',
    notes TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_intermediaries_kind ON intermediaries (kind);
CREATE INDEX IF NOT EXISTS ix_intermediaries_is_active
    ON intermediaries (is_active);
