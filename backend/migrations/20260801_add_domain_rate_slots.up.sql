-- Общая на все worker-процессы очередь обращений к внешнему домену.
-- Пауза между запросами к одному хосту раньше жила в памяти процесса, поэтому
-- при нескольких репликах суммарная частота к поисковой выдаче и PubChem
-- росла кратно их числу.

CREATE TABLE IF NOT EXISTS domain_rate_slots (
    host VARCHAR(255) PRIMARY KEY,
    -- Epoch в секундах: переносимо между диалектами и не зависит от того,
    -- как каждый из них округляет часовые пояса.
    next_allowed_at DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
