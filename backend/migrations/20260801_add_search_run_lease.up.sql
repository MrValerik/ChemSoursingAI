-- Аренда задач очереди поиска. Без неё второй worker при старте помечает
-- failed чужие выполняющиеся задачи: recover_interrupted_jobs не различал,
-- кому принадлежит незавершённый запуск.

ALTER TABLE search_runs
    ADD COLUMN IF NOT EXISTS lease_owner VARCHAR(128);

ALTER TABLE search_runs
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

-- Fencing token: номер поколения аренды. Зависший worker, чья аренда была
-- перевыдана, не должен записать результат поверх нового исполнителя.
ALTER TABLE search_runs
    ADD COLUMN IF NOT EXISTS lease_generation INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS ix_search_runs_lease_expires_at
    ON search_runs (lease_expires_at);
